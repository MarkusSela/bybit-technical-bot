import os, json, math, glob
import pandas as pd
import ta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
DATA_DIR         = Path('user_data/data/bybit')
STARTING_BALANCE = 780.0
MIN_VALUE        = 150.0
FEE_PCT          = 0.0011       # 0.11% round trip
RISK_PCT         = 0.02         # 2% del capitale per trade
LEVERAGE         = 20           # leva fissa per ora
SL_PCT           = 0.004        # 0.4% SL stretto
RR_RATIOS        = [3, 5, 10]   # R:R da testare

# Segnale momentum 5m:
# - Corpo candela > MIN_BODY_PCT del range (candela forte)
# - Volume > VOLUME_MULT * media 20 candele
# - Breakout del massimo/minimo delle ultime BREAKOUT_CANDLES candele
MIN_BODY_PCT     = 0.6          # corpo >= 60% del range high-low
VOLUME_MULT      = 1.5          # volume >= 1.5x media
BREAKOUT_CANDLES = 10           # rottura max/min ultime 10 candele

PAIRS = [
    'BTC/USDT:USDT',  'ETH/USDT:USDT',  'BNB/USDT:USDT',
    'SOL/USDT:USDT',  'XRP/USDT:USDT',  'DOGE/USDT:USDT',
    'ADA/USDT:USDT',  'AVAX/USDT:USDT', 'LINK/USDT:USDT',
    'DOT/USDT:USDT',  'UNI/USDT:USDT',  'ATOM/USDT:USDT',
    'LTC/USDT:USDT',  'BCH/USDT:USDT',  'FIL/USDT:USDT',
    'NEAR/USDT:USDT', 'APT/USDT:USDT',  'ARB/USDT:USDT',
    'OP/USDT:USDT',   'SUI/USDT:USDT',  'INJ/USDT:USDT',
    'TIA/USDT:USDT',  'WLD/USDT:USDT',  'XAUT/USDT:USDT',
    'PEPE/USDT:USDT',
]

# ── Data loader ────────────────────────────────────────────────────────────
def load_pair(symbol, timeframe):
    name    = symbol.replace('/', '_').replace(':', '_')
    pattern = str(DATA_DIR / 'futures' / (name + '-' + timeframe + '*'))
    files   = glob.glob(pattern)
    if not files:
        # prova senza futures/
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

# ── Segnale momentum ────────────────────────────────────────────────────────
def get_signal(df, i):
    """
    Ritorna 'LONG', 'SHORT' o None.

    Condizioni LONG:
    - Candela bullish (close > open)
    - Corpo >= MIN_BODY_PCT del range (candela forte, non doji)
    - Volume >= VOLUME_MULT * media 20 candele precedenti
    - Close rompe il massimo delle ultime BREAKOUT_CANDLES candele

    Condizioni SHORT: speculari.
    """
    if i < BREAKOUT_CANDLES + 20:
        return None

    candle = df.iloc[i]
    body   = abs(candle['close'] - candle['open'])
    rng    = candle['high'] - candle['low']

    if rng == 0:
        return None

    # Corpo forte
    if body / rng < MIN_BODY_PCT:
        return None

    # Volume spike
    avg_vol = df['volume'].iloc[i - 20:i].mean()
    if avg_vol == 0:
        return None
    if candle['volume'] < VOLUME_MULT * avg_vol:
        return None

    # Breakout
    prev_high = df['high'].iloc[i - BREAKOUT_CANDLES:i].max()
    prev_low  = df['low'].iloc[i - BREAKOUT_CANDLES:i].min()

    if candle['close'] > candle['open'] and candle['close'] > prev_high:
        return 'LONG'
    elif candle['close'] < candle['open'] and candle['close'] < prev_low:
        return 'SHORT'

    return None

# ── Simulazione ─────────────────────────────────────────────────────────────
def simulate(df, rr):
    trades   = []
    in_trade = False
    entry = sl = tp = 0
    direction = ''

    for i in range(BREAKOUT_CANDLES + 20, len(df)):
        if in_trade:
            high_c = df['high'].iloc[i]
            low_c  = df['low'].iloc[i]

            if direction == 'LONG':
                if low_c <= sl:
                    pnl = -SL_PCT * 100 - FEE_PCT * 100
                    trades.append({'result': 'LOSS', 'pnl': pnl})
                    in_trade = False
                elif high_c >= tp:
                    pnl = SL_PCT * rr * 100 - FEE_PCT * 100
                    trades.append({'result': 'WIN', 'pnl': pnl})
                    in_trade = False
            else:
                if high_c >= sl:
                    pnl = -SL_PCT * 100 - FEE_PCT * 100
                    trades.append({'result': 'LOSS', 'pnl': pnl})
                    in_trade = False
                elif low_c <= tp:
                    pnl = SL_PCT * rr * 100 - FEE_PCT * 100
                    trades.append({'result': 'WIN', 'pnl': pnl})
                    in_trade = False
            continue

        sig = get_signal(df, i)
        if sig is None:
            continue

        price     = df['close'].iloc[i]
        direction = sig
        entry     = price

        if direction == 'LONG':
            sl = price * (1 - SL_PCT)
            tp = price * (1 + SL_PCT * rr)
        else:
            sl = price * (1 + SL_PCT)
            tp = price * (1 - SL_PCT * rr)

        # Verifica MIN_VALUE
        trade_value = STARTING_BALANCE * RISK_PCT * LEVERAGE
        if trade_value < MIN_VALUE:
            continue

        in_trade = True

    return trades

# ── Main ────────────────────────────────────────────────────────────────────
print('=' * 80)
print('BACKTEST 5M MOMENTUM | SL=' + str(int(SL_PCT*100*10)/10) + '% | Leva ' + str(LEVERAGE) + 'x | 180 giorni')
print('Segnale: corpo forte (' + str(int(MIN_BODY_PCT*100)) + '%) + volume ' + str(VOLUME_MULT) + 'x + breakout ' + str(BREAKOUT_CANDLES) + ' candele')
print('R:R testati: ' + str(RR_RATIOS))
print('=' * 80)

# Header tabella
print('{:<20} {:>8} {:>8} {:>10} {:>10} {:>10}'.format(
    'Pair', 'Trades', 'WR%', 'RR1:'+str(RR_RATIOS[0]), 'RR1:'+str(RR_RATIOS[1]), 'RR1:'+str(RR_RATIOS[2])))
print('-' * 80)

all_results = []

for pair in PAIRS:
    df = load_pair(pair, '5m')
    if df is None or len(df) < 500:
        print('{:<20} dati mancanti'.format(pair.split('/')[0]))
        continue

    results_by_rr = {}
    for rr in RR_RATIOS:
        trades = simulate(df, rr)
        if not trades:
            results_by_rr[rr] = {'trades': 0, 'wr': 0, 'pnl': 0}
            continue
        wins = sum(1 for t in trades if t['result'] == 'WIN')
        pnl  = sum(t['pnl'] for t in trades)
        wr   = round(wins / len(trades) * 100, 1)
        results_by_rr[rr] = {'trades': len(trades), 'wr': wr, 'pnl': round(pnl, 1)}

    base = results_by_rr[RR_RATIOS[0]]
    pnl_strs = []
    for rr in RR_RATIOS:
        p = results_by_rr[rr]['pnl']
        pnl_strs.append(('+' if p >= 0 else '') + str(p) + '%')

    print('{:<20} {:>8} {:>8} {:>10} {:>10} {:>10}'.format(
        pair.split('/')[0],
        base['trades'],
        str(base['wr']) + '%',
        pnl_strs[0],
        pnl_strs[1],
        pnl_strs[2],
    ))

    all_results.append({'pair': pair, 'rr_data': results_by_rr})

print('=' * 80)

# Totali per R:R
print()
print('TOTALI PER R:R:')
for rr in RR_RATIOS:
    tot_trades = sum(r['rr_data'][rr]['trades'] for r in all_results)
    tot_pnl    = round(sum(r['rr_data'][rr]['pnl'] for r in all_results), 1)
    positivi   = sum(1 for r in all_results if r['rr_data'][rr]['pnl'] > 0)
    if tot_trades > 0:
        tot_wr = round(sum(r['rr_data'][rr]['wr'] * r['rr_data'][rr]['trades']
                           for r in all_results) / tot_trades, 1)
    else:
        tot_wr = 0
    tag = ' ✓' if tot_pnl > 0 else ' ✗'
    print('  R:R 1:' + str(rr) + ' | Trade: ' + str(tot_trades) +
          ' | WR: ' + str(tot_wr) + '% | PnL totale: ' + str(tot_pnl) + '%' + tag)

# Top 5 per ogni R:R
print()
for rr in RR_RATIOS:
    print('TOP 5 R:R 1:' + str(rr) + ':')
    sorted_r = sorted(all_results, key=lambda x: x['rr_data'][rr]['pnl'], reverse=True)
    for r in sorted_r[:5]:
        d = r['rr_data'][rr]
        tag = ' ✓' if d['pnl'] > 0 else ' ✗'
        print('  ' + r['pair'].split('/')[0] + ' | WR: ' + str(d['wr']) +
              '% | PnL: ' + str(d['pnl']) + '%' + tag)
    print()

print('=' * 80)
print('SL stretto = ' + str(round(SL_PCT * 100, 2)) + '% | TP = SL x R:R')
print('Ogni candela da 5 minuti e una potenziale entrata se tutti i segnali si allineano')
