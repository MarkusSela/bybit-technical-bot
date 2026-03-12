import os, json, math, glob
import pandas as pd
import ta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
CANDLES_LOOKBACK = 100
FG_DEFAULT       = 50
DATA_DIR         = Path('user_data/data/bybit')
FEE_PCT          = 0.0011

STARTING_BALANCE = 780.0
MIN_VALUE        = 150.0
MIN_LEVERAGE     = 5

SCORE_PARAMS = {
    6: {'sl': 0.008, 'tp': 0.030, 'risk_pct': 0.25},
    5: {'sl': 0.012, 'tp': 0.024, 'risk_pct': 0.15},
    3: {'sl': 0.018, 'tp': 0.020, 'risk_pct': 0.07},
}

def get_params(score):
    if score >= 6:
        return SCORE_PARAMS[6]
    elif score >= 5:
        return SCORE_PARAMS[5]
    else:
        return SCORE_PARAMS[3]

def get_leverage(score, fg_value=50):
    if fg_value < 25:
        max_lev = 10
    elif fg_value < 40:
        max_lev = 20
    else:
        max_lev = 50
    if score >= 6:
        return max_lev
    elif score >= 5:
        return max(MIN_LEVERAGE, max_lev // 2)
    else:
        return MIN_LEVERAGE

# ── Indicatori ─────────────────────────────────────────────────────────────
def compute_indicators(df):
    close = df['close']
    high  = df['high']
    low   = df['low']
    vol   = df['volume']
    rsi   = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    macd  = ta.trend.MACD(close)
    bb    = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper  = bb.bollinger_hband().iloc[-1]
    bb_lower  = bb.bollinger_lband().iloc[-1]
    bb_pct    = (close.iloc[-1] - bb_lower) / (bb_upper - bb_lower + 1e-9)
    ema20     = ta.trend.EMAIndicator(close, window=20).ema_indicator().iloc[-1]
    ema50     = ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1]
    avg_vol   = vol.rolling(20).mean().iloc[-1]
    vol_ratio = vol.iloc[-1] / avg_vol if avg_vol and avg_vol > 0 else 1.0
    macd_hist = macd.macd_diff().iloc[-1]
    macd_prev = macd.macd_diff().iloc[-2]
    if macd_hist > 0 and macd_prev <= 0:
        macd_cross = 'bullish'
    elif macd_hist < 0 and macd_prev >= 0:
        macd_cross = 'bearish'
    else:
        macd_cross = 'none'
    chg_4h   = round((close.iloc[-1] - close.iloc[-16]) / close.iloc[-16] * 100, 3) if len(close) >= 16 else 0
    trend_4h = 'bearish' if chg_4h < 0 else 'bullish'
    if chg_4h < -3:
        trend_bias = 'strong_bear'
    elif chg_4h < 0:
        trend_bias = 'bear'
    elif chg_4h > 3:
        trend_bias = 'strong_bull'
    else:
        trend_bias = 'bull'
    return {
        'rsi_14':       round(rsi, 2),
        'macd_cross':   macd_cross,
        'bb_pct':       round(bb_pct, 3),
        'ema_trend':    'bullish' if ema20 > ema50 else 'bearish',
        'volume_ratio': round(vol_ratio, 2),
        'trend_4h':     trend_4h,
        'trend_bias':   trend_bias,
        'close':        close.iloc[-1],
    }

def signal_score(ind, fg_value):
    score = 0
    if ind['rsi_14'] < 32 or ind['rsi_14'] > 68:
        score += 1
    if ind['macd_cross'] in ('bullish', 'bearish'):
        score += 2
    if ind['volume_ratio'] > 1.8:
        score += 1
    if ind['bb_pct'] < 0.1 or ind['bb_pct'] > 0.9:
        score += 1
    if ind['ema_trend'] == 'bearish' and ind['trend_4h'] == 'bearish':
        score += 1
    elif ind['ema_trend'] == 'bullish' and ind['trend_4h'] == 'bullish':
        score += 1
    if fg_value < 25 and ind['trend_bias'] in ('bear', 'strong_bear'):
        score += 1
    return score

# ── Data loader ────────────────────────────────────────────────────────────
def load_pair(symbol, timeframe):
    name    = symbol.replace('/', '_')
    pattern = str(DATA_DIR / (name + '-' + timeframe + '*'))
    files   = glob.glob(pattern)
    if not files:
        return None
    f = files[0]
    if f.endswith('.json'):
        raw = json.load(open(f))
        df  = pd.DataFrame(raw, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    else:
        df = pd.read_feather(f)
    df = df.sort_values('date').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    return df

def get_daily_trend(df_daily, current_ts):
    try:
        past = df_daily[df_daily['date'] <= current_ts]
        if len(past) < 20:
            return 'neutral'
        close = past['close']
        ema20 = ta.trend.EMAIndicator(close, window=20).ema_indicator().iloc[-1]
        ema50 = ta.trend.EMAIndicator(close, window=min(50, len(close))).ema_indicator().iloc[-1]
        last  = close.iloc[-1]
        if last > ema20 and ema20 > ema50:
            return 'bullish'
        elif last < ema20 and ema20 < ema50:
            return 'bearish'
        else:
            return 'neutral'
    except:
        return 'neutral'

# ── Simulazione REVERSED ───────────────────────────────────────────────────
# LOGICA INVERTITA:
# - Se il segnale tecnico dice LONG  → apriamo SHORT
# - Se il segnale tecnico dice SHORT → apriamo LONG
# - Il filtro daily viene anche lui invertito:
#   daily bullish + segnale LONG (ora SHORT) → lasciamo passare il SHORT
#   daily bearish + segnale SHORT (ora LONG) → lasciamo passare il LONG
# In pratica: nessun filtro daily (se il segnale tecnico esiste, entra al contrario)
def simulate_reversed(df_15m, df_daily):
    trades      = []
    blocked     = 0
    in_trade    = False
    direction   = ''
    entry = sl = tp = 0
    cur_score   = 0
    risk_pct    = 0.07
    leverage    = MIN_LEVERAGE

    for i in range(CANDLES_LOOKBACK, len(df_15m)):
        window     = df_15m.iloc[i - CANDLES_LOOKBACK:i].reset_index(drop=True)
        current_ts = df_15m['date'].iloc[i]

        if in_trade:
            high_c = df_15m['high'].iloc[i]
            low_c  = df_15m['low'].iloc[i]
            if direction == 'LONG':
                if low_c <= sl:
                    pnl = (-abs(entry - sl) / entry * 100 - FEE_PCT * 100)
                    trades.append({'result': 'LOSS', 'pnl': pnl, 'score': cur_score})
                    in_trade = False
                elif high_c >= tp:
                    pnl = (abs(tp - entry) / entry * 100 - FEE_PCT * 100)
                    trades.append({'result': 'WIN', 'pnl': pnl, 'score': cur_score})
                    in_trade = False
            else:
                if high_c >= sl:
                    pnl = (-abs(sl - entry) / entry * 100 - FEE_PCT * 100)
                    trades.append({'result': 'LOSS', 'pnl': pnl, 'score': cur_score})
                    in_trade = False
                elif low_c <= tp:
                    pnl = (abs(entry - tp) / entry * 100 - FEE_PCT * 100)
                    trades.append({'result': 'WIN', 'pnl': pnl, 'score': cur_score})
                    in_trade = False
            continue

        try:
            ind = compute_indicators(window)
        except:
            continue

        cur_score = signal_score(ind, FG_DEFAULT)
        if cur_score < 3:
            continue

        # Direzione tecnica originale
        if ind['trend_4h'] == 'bullish' and ind['ema_trend'] == 'bullish':
            original_direction = 'LONG'
        elif ind['trend_4h'] == 'bearish' and ind['ema_trend'] == 'bearish':
            original_direction = 'SHORT'
        else:
            continue

        # ── INVERSIONE ──────────────────────────────────────────────────────
        # Facciamo esattamente il contrario del segnale tecnico
        reversed_direction = 'SHORT' if original_direction == 'LONG' else 'LONG'

        # Filtro daily invertito coerentemente:
        # se daily bullish blocchiamo i LONG (che ora sono il contrario dei SHORT originali)
        if df_daily is not None:
            daily_trend = get_daily_trend(df_daily, current_ts)
            if daily_trend == 'bullish' and reversed_direction == 'SHORT':
                continue
            if daily_trend == 'bearish' and reversed_direction == 'LONG':
                continue

        params   = get_params(cur_score)
        risk_pct = params['risk_pct']
        leverage = get_leverage(cur_score, FG_DEFAULT)
        price    = ind['close']

        trade_value = STARTING_BALANCE * risk_pct * leverage
        if trade_value < MIN_VALUE:
            blocked += 1
            continue

        direction = reversed_direction
        entry     = price
        sl        = round(entry * (1 - params['sl']) if direction == 'LONG' else entry * (1 + params['sl']), 6)
        tp        = round(entry * (1 + params['tp']) if direction == 'LONG' else entry * (1 - params['tp']), 6)
        in_trade  = True

    return trades, blocked

# ── Pairs ──────────────────────────────────────────────────────────────────
PAIRS = [
    'BTC/USDT',  'ETH/USDT',   'BNB/USDT',  'SOL/USDT',
    'XRP/USDT',  'DOGE/USDT',  'ADA/USDT',  'AVAX/USDT',
    'LINK/USDT', 'DOT/USDT',   'UNI/USDT',  'ATOM/USDT',
    'LTC/USDT',  'BCH/USDT',   'FIL/USDT',  'NEAR/USDT',
    'APT/USDT',  'ARB/USDT',   'OP/USDT',   'SUI/USDT',
    'INJ/USDT',  'TIA/USDT',   'WLD/USDT',
    'XAUT/USDT', '1000PEPE/USDT',
]

print('=' * 70)
print('BACKTEST REVERSED | Balance $' + str(STARTING_BALANCE) + ' | MIN_VALUE $' + str(MIN_VALUE))
print('Direzione INVERTITA rispetto al segnale tecnico | Filtro daily ON invertito')
print('Fee incluse | SL/TP dinamici per score | 180 giorni')
print('=' * 70)

results = []

for pair in PAIRS:
    df_15m   = load_pair(pair, '15m')
    df_daily = load_pair(pair, '1d')
    if df_15m is None or len(df_15m) < CANDLES_LOOKBACK + 50:
        print(pair + ': dati 15m mancanti -- skip')
        continue
    if df_daily is None:
        print(pair + ': dati daily mancanti -- skip')
        continue

    trades, blocked = simulate_reversed(df_15m, df_daily)

    if not trades:
        print(pair + ': nessun segnale')
        continue

    wins  = sum(1 for t in trades if t['result'] == 'WIN')
    pnl   = sum(t['pnl'] for t in trades)
    wr    = round(wins / len(trades) * 100, 1)
    pnl_r = round(pnl, 1)
    tag   = ' ✓' if pnl_r > 0 else ' ✗'
    block_str = ' [' + str(blocked) + ' bloccati MIN]' if blocked > 0 else ''

    results.append({'pair': pair, 'trades': len(trades), 'wr': wr, 'pnl': pnl_r, 'blocked': blocked})
    print(pair + ' | Trade: ' + str(len(trades)) + ' | WR: ' + str(wr) + '% | PnL: ' + str(pnl_r) + '%' + block_str + tag)

print()
print('=' * 70)
results.sort(key=lambda x: x['pnl'], reverse=True)
print('CLASSIFICA REVERSED PER PnL:')
for i, r in enumerate(results):
    tag = ' ✓' if r['pnl'] > 0 else ' ✗'
    print(str(i+1) + '. ' + r['pair'] + ' | WR: ' + str(r['wr']) + '% | PnL: ' + str(r['pnl']) + '%' + tag)

positivi = [r for r in results if r['pnl'] > 0]
print()
print('TOP POSITIVI REVERSED:')
for r in positivi[:6]:
    print('  ' + r['pair'] + ' | WR: ' + str(r['wr']) + '% | PnL: ' + str(r['pnl']) + '%')

print()
tot_trades  = sum(r['trades'] for r in results)
tot_pnl     = round(sum(r['pnl'] for r in results), 1)
tot_wr      = round(sum(r['wr'] * r['trades'] for r in results) / tot_trades, 1) if tot_trades > 0 else 0
tot_blocked = sum(r['blocked'] for r in results)

print('TOTALE REVERSED | Trade: ' + str(tot_trades) + ' | WR medio: ' + str(tot_wr) + '% | PnL: ' + str(tot_pnl) + '%')
print()
print('Trade bloccati da MIN_VALUE: ' + str(tot_blocked))
print('=' * 70)
