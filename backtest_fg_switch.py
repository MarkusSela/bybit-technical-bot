import json, glob, math, requests, time
import pandas as pd
import ta
from pathlib import Path
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────────────────
DATA_DIR         = Path('user_data/data/bybit')
FEE_PCT          = 0.0011
STARTING_BALANCE = 780.0
MIN_VALUE        = 150.0
MIN_LEVERAGE     = 5

# Soglie F&G per switch strategia
FG_FEAR_MAX      = 25   # sotto questo → fear strategy
FG_GREED_MIN     = 50   # sopra questo → greed strategy
                        # tra 25 e 50  → neutral strategy

# Strategia fear: soglie distanza da EMA20 per entrare
FEAR_THRESHOLDS  = [0.015, 0.025, 0.040]  # 1.5%, 2.5%, 4.0%
FEAR_SL_PCT      = 0.010                   # SL 1%

# Strategia trend (neutral/greed): parametri dal bot attuale
SCORE_PARAMS = {
    6: {'sl': 0.008, 'tp': 0.030, 'risk_pct': 0.25},
    5: {'sl': 0.012, 'tp': 0.024, 'risk_pct': 0.15},
    3: {'sl': 0.018, 'tp': 0.030, 'risk_pct': 0.07},
}

PAIRS = [
    'BTC/USDT:USDT',  'ETH/USDT:USDT',  'SOL/USDT:USDT',
    'XRP/USDT:USDT',  'DOGE/USDT:USDT', 'ADA/USDT:USDT',
    'AVAX/USDT:USDT', 'LINK/USDT:USDT', 'ARB/USDT:USDT',
    'SUI/USDT:USDT',  'WLD/USDT:USDT',  'XAUT/USDT:USDT',
]

# ── Scarica storico F&G ────────────────────────────────────────────────────
def fetch_fg_history(days=180):
    print('Scaricando storico Fear & Greed (' + str(days) + ' giorni)...')
    try:
        r = requests.get(
            'https://api.alternative.me/fng/?limit=' + str(days),
            timeout=10
        )
        data = r.json()['data']
        fg = {}
        for d in data:
            ts    = int(d['timestamp'])
            date  = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
            fg[date] = int(d['value'])
        print('F&G scaricato: ' + str(len(fg)) + ' giorni')
        return fg
    except Exception as e:
        print('Errore F&G: ' + str(e) + ' — uso F&G=50 fisso')
        return {}

def get_fg_for_ts(fg_history, timestamp_ms):
    try:
        dt   = datetime.utcfromtimestamp(timestamp_ms / 1000)
        date = dt.strftime('%Y-%m-%d')
        if date in fg_history:
            return fg_history[date]
        # Cerca il giorno precedente
        for i in range(1, 8):
            prev = (dt - timedelta(days=i)).strftime('%Y-%m-%d')
            if prev in fg_history:
                return fg_history[prev]
    except:
        pass
    return 50

# ── Data loader ────────────────────────────────────────────────────────────
def load_pair(symbol, timeframe):
    name    = symbol.replace('/', '_').replace(':', '_')
    pattern = str(DATA_DIR / 'futures' / (name + '-' + timeframe + '*'))
    files   = glob.glob(pattern)
    if not files:
        pattern = str(DATA_DIR / (name + '-' + timeframe + '*'))
        files   = glob.glob(pattern)
    if not files:
        return None
    f = files[0]
    if f.endswith('.json'):
        raw = json.load(open(f))
        df  = pd.DataFrame(raw, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    elif f.endswith('.feather'):
        df = pd.read_feather(f)
    else:
        return None
    df = df.sort_values('date').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    return df

# ── Indicatori trend ────────────────────────────────────────────────────────
def compute_indicators(df, i, lookback=100):
    start  = max(0, i - lookback)
    window = df.iloc[start:i].reset_index(drop=True)
    close  = window['close']
    high   = window['high']
    low    = window['low']
    vol    = window['volume']

    if len(close) < 52:
        return None

    rsi       = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    macd      = ta.trend.MACD(close)
    bb        = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper  = bb.bollinger_hband().iloc[-1]
    bb_lower  = bb.bollinger_lband().iloc[-1]
    bb_pct    = (close.iloc[-1] - bb_lower) / (bb_upper - bb_lower + 1e-9)
    ema20     = ta.trend.EMAIndicator(close, window=20).ema_indicator().iloc[-1]
    ema50     = ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1]
    avg_vol   = vol.rolling(20).mean().iloc[-1]
    vol_ratio = vol.iloc[-1] / avg_vol if avg_vol and avg_vol > 0 else 1.0
    macd_hist = macd.macd_diff().iloc[-1]
    macd_prev = macd.macd_diff().iloc[-2] if len(close) > 2 else 0
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
        'price':        close.iloc[-1],
        'rsi_14':       rsi,
        'macd_cross':   macd_cross,
        'bb_pct':       bb_pct,
        'ema_trend':    'bullish' if ema20 > ema50 else 'bearish',
        'ema20':        ema20,
        'volume_ratio': vol_ratio,
        'trend_4h':     trend_4h,
        'trend_bias':   trend_bias,
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

def get_direction(ind):
    if ind['trend_4h'] == 'bullish' and ind['ema_trend'] == 'bullish':
        return 'LONG'
    elif ind['trend_4h'] == 'bearish' and ind['ema_trend'] == 'bearish':
        return 'SHORT'
    return None

def get_params(score):
    if score >= 6:
        return SCORE_PARAMS[6]
    elif score >= 5:
        return SCORE_PARAMS[5]
    else:
        return SCORE_PARAMS[3]

def get_leverage(fg_value):
    if fg_value < 25:
        return 10
    elif fg_value < 40:
        return 20
    else:
        return 50

# ── Simulazione con switch F&G ─────────────────────────────────────────────
def simulate_fg_switch(df, fg_history, fear_threshold):
    trades_fear   = []
    trades_trend  = []
    in_trade      = False
    direction     = ''
    entry = sl = tp = ema20_target = 0
    current_strategy = ''
    LOOKBACK = 100

    for i in range(LOOKBACK, len(df)):
        ts      = df['date'].iloc[i]
        fg      = get_fg_for_ts(fg_history, ts)
        high_c  = df['high'].iloc[i]
        low_c   = df['low'].iloc[i]
        close_c = df['close'].iloc[i]

        if in_trade:
            if direction == 'LONG':
                if low_c <= sl:
                    pnl = -abs(entry - sl) / entry * 100 - FEE_PCT * 100
                    if current_strategy == 'fear':
                        trades_fear.append({'result': 'LOSS', 'pnl': pnl})
                    else:
                        trades_trend.append({'result': 'LOSS', 'pnl': pnl})
                    in_trade = False
                elif high_c >= tp:
                    pnl = abs(tp - entry) / entry * 100 - FEE_PCT * 100
                    if current_strategy == 'fear':
                        trades_fear.append({'result': 'WIN', 'pnl': pnl})
                    else:
                        trades_trend.append({'result': 'WIN', 'pnl': pnl})
                    in_trade = False
            else:
                if high_c >= sl:
                    pnl = -abs(sl - entry) / entry * 100 - FEE_PCT * 100
                    if current_strategy == 'fear':
                        trades_fear.append({'result': 'LOSS', 'pnl': pnl})
                    else:
                        trades_trend.append({'result': 'LOSS', 'pnl': pnl})
                    in_trade = False
                elif low_c <= tp:
                    pnl = abs(entry - tp) / entry * 100 - FEE_PCT * 100
                    if current_strategy == 'fear':
                        trades_fear.append({'result': 'WIN', 'pnl': pnl})
                    else:
                        trades_trend.append({'result': 'WIN', 'pnl': pnl})
                    in_trade = False
            continue

        ind = compute_indicators(df, i, LOOKBACK)
        if ind is None:
            continue

        price = ind['price']
        ema20 = ind['ema20']

        # ── FEAR strategy ──────────────────────────────────────────────────
        if fg < FG_FEAR_MAX:
            dist = (price - ema20) / ema20

            if dist < -fear_threshold:
                # Prezzo troppo sotto EMA20 → LONG, TP = EMA20
                direction        = 'LONG'
                entry            = price
                sl               = price * (1 - FEAR_SL_PCT)
                tp               = ema20
                ema20_target     = ema20
                current_strategy = 'fear'
                if tp > entry and tp - entry > entry * 0.002:
                    in_trade = True

            elif dist > fear_threshold:
                # Prezzo troppo sopra EMA20 → SHORT, TP = EMA20
                direction        = 'SHORT'
                entry            = price
                sl               = price * (1 + FEAR_SL_PCT)
                tp               = ema20
                ema20_target     = ema20
                current_strategy = 'fear'
                if tp < entry and entry - tp > entry * 0.002:
                    in_trade = True

        # ── TREND strategy (neutral + greed) ──────────────────────────────
        else:
            score = signal_score(ind, fg)
            if score < 3:
                continue
            dir_trend = get_direction(ind)
            if dir_trend is None:
                continue

            params   = get_params(score)
            leverage = get_leverage(fg)
            sl_pct   = params['sl']
            tp_pct   = params['tp']

            if leverage >= 20 and sl_pct > 0.003:
                sl_pct = 0.003

            direction        = dir_trend
            entry            = price
            current_strategy = 'trend'

            if direction == 'LONG':
                sl = entry * (1 - sl_pct)
                tp = entry * (1 + tp_pct)
            else:
                sl = entry * (1 + sl_pct)
                tp = entry * (1 - tp_pct)

            in_trade = True

    return trades_fear, trades_trend

# ── Main ───────────────────────────────────────────────────────────────────
fg_history = fetch_fg_history(180)

print()
print('=' * 90)
print('BACKTEST F&G SWITCH | F&G<25=Fear(MeanReversion) | F&G>=25=Trend | 180 giorni')
print('Soglie EMA20 testate: ' + str([str(int(t*100))+'%' for t in FEAR_THRESHOLDS]))
print('SL fear: ' + str(int(FEAR_SL_PCT*100)) + '% | TP fear: ritorno EMA20')
print('=' * 90)

for threshold in FEAR_THRESHOLDS:
    print()
    print('── SOGLIA FEAR ' + str(int(threshold*100)) + '% ─────────────────────────────────────────────')
    print('{:<12} {:>7} {:>8} {:>10} {:>7} {:>8} {:>10}'.format(
        'Pair', 'F.trade', 'F.WR%', 'F.PnL%', 'T.trade', 'T.WR%', 'T.PnL%'))
    print('-' * 70)

    tot_ft = tot_fw = tot_fp = 0
    tot_tt = tot_tw = tot_tp = 0

    for pair in PAIRS:
        df = load_pair(pair, '15m')
        if df is None or len(df) < 200:
            print('{:<12} dati mancanti'.format(pair.split('/')[0]))
            continue

        tf, tt = simulate_fg_switch(df, fg_history, threshold)

        def stats(trades):
            if not trades:
                return 0, 0.0, 0.0
            wins = sum(1 for t in trades if t['result'] == 'WIN')
            pnl  = round(sum(t['pnl'] for t in trades), 1)
            wr   = round(wins / len(trades) * 100, 1)
            return len(trades), wr, pnl

        fn, fwr, fpnl = stats(tf)
        tn, twr, tpnl = stats(tt)

        tot_ft += fn; tot_fw += sum(1 for t in tf if t['result'] == 'WIN'); tot_fp += fpnl
        tot_tt += tn; tot_tw += sum(1 for t in tt if t['result'] == 'WIN'); tot_tp += tpnl

        ftag = '+' if fpnl >= 0 else ''
        ttag = '+' if tpnl >= 0 else ''

        print('{:<12} {:>7} {:>8} {:>10} {:>7} {:>8} {:>10}'.format(
            pair.split('/')[0],
            fn,
            str(fwr) + '%',
            ftag + str(fpnl) + '%',
            tn,
            str(twr) + '%',
            ttag + str(tpnl) + '%',
        ))

    fwr_tot = round(tot_fw / tot_ft * 100, 1) if tot_ft > 0 else 0
    twr_tot = round(tot_tw / tot_tt * 100, 1) if tot_tt > 0 else 0
    fp_tag  = ' ✓' if tot_fp >= 0 else ' ✗'
    tp_tag  = ' ✓' if tot_tp >= 0 else ' ✗'

    print('-' * 70)
    print('FEAR  | Trade: ' + str(tot_ft) + ' | WR: ' + str(fwr_tot) + '% | PnL: ' + str(round(tot_fp,1)) + '%' + fp_tag)
    print('TREND | Trade: ' + str(tot_tt) + ' | WR: ' + str(twr_tot) + '% | PnL: ' + str(round(tot_tp,1)) + '%' + tp_tag)
    print('TOTALE| PnL combinato: ' + str(round(tot_fp + tot_tp, 1)) + '%' + (' ✓' if tot_fp + tot_tp >= 0 else ' ✗'))

print()
print('=' * 90)
print('Legenda: F.=strategia Fear | T.=strategia Trend')
print('Fear entra quando prezzo si allontana da EMA20 della soglia indicata')
print('Trend usa logica bot attuale (MACD+RSI+BB+EMA)')
