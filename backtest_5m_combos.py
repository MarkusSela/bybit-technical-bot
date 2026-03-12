import os, json, math, glob
import pandas as pd
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
DATA_DIR         = Path('user_data/data/bybit')
FEE_PCT          = 0.0011
SL_PCT           = 0.004
RR               = 10           # fissiamo R:R 1:10 che era il migliore

# Combinazioni di parametri da testare
COMBOS = [
    {'name': 'A baseline      ', 'body': 0.60, 'vol': 1.5, 'brk': 10, 'trend': False},
    {'name': 'B medio         ', 'body': 0.70, 'vol': 2.0, 'brk': 20, 'trend': False},
    {'name': 'C stretto       ', 'body': 0.80, 'vol': 3.0, 'brk': 30, 'trend': False},
    {'name': 'D medio+trend   ', 'body': 0.70, 'vol': 2.0, 'brk': 20, 'trend': True},
    {'name': 'E stretto+trend ', 'body': 0.80, 'vol': 3.0, 'brk': 30, 'trend': True},
]

PAIRS = [
    'BTC/USDT:USDT',  'ETH/USDT:USDT',  'BNB/USDT:USDT',
    'SOL/USDT:USDT',  'XRP/USDT:USDT',  'DOGE/USDT:USDT',
    'ADA/USDT:USDT',  'AVAX/USDT:USDT', 'LINK/USDT:USDT',
    'DOT/USDT:USDT',  'UNI/USDT:USDT',  'ATOM/USDT:USDT',
    'LTC/USDT:USDT',  'BCH/USDT:USDT',  'FIL/USDT:USDT',
    'NEAR/USDT:USDT', 'APT/USDT:USDT',  'ARB/USDT:USDT',
    'OP/USDT:USDT',   'SUI/USDT:USDT',  'INJ/USDT:USDT',
    'TIA/USDT:USDT',  'WLD/USDT:USDT',  'XAUT/USDT:USDT',
]

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

# ── Segnale ────────────────────────────────────────────────────────────────
def get_signal(df, i, body_pct, vol_mult, brk_candles, use_trend):
    min_lookback = brk_candles + 20
    if i < min_lookback:
        return None

    candle = df.iloc[i]
    body   = abs(candle['close'] - candle['open'])
    rng    = candle['high'] - candle['low']
    if rng == 0:
        return None

    # Corpo forte
    if body / rng < body_pct:
        return None

    # Volume spike
    avg_vol = df['volume'].iloc[i - 20:i].mean()
    if avg_vol == 0 or candle['volume'] < vol_mult * avg_vol:
        return None

    # Breakout
    prev_high = df['high'].iloc[i - brk_candles:i].max()
    prev_low  = df['low'].iloc[i - brk_candles:i].min()

    if candle['close'] > candle['open'] and candle['close'] > prev_high:
        direction = 'LONG'
    elif candle['close'] < candle['open'] and candle['close'] < prev_low:
        direction = 'SHORT'
    else:
        return None

    # Filtro trend: ultime 3 candele nella stessa direzione
    if use_trend:
        last3 = df.iloc[i-3:i]
        if direction == 'LONG':
            if not all(last3['close'].values > last3['open'].values):
                return None
        else:
            if not all(last3['close'].values < last3['open'].values):
                return None

    return direction

# ── Simulazione ────────────────────────────────────────────────────────────
def simulate(df, combo):
    trades   = []
    in_trade = False
    entry = sl = tp = 0
    direction = ''
    body_pct   = combo['body']
    vol_mult   = combo['vol']
    brk_candles = combo['brk']
    use_trend  = combo['trend']
    min_lookback = brk_candles + 20

    for i in range(min_lookback, len(df)):
        if in_trade:
            high_c = df['high'].iloc[i]
            low_c  = df['low'].iloc[i]
            if direction == 'LONG':
                if low_c <= sl:
                    trades.append({'result': 'LOSS', 'pnl': -SL_PCT * 100 - FEE_PCT * 100})
                    in_trade = False
                elif high_c >= tp:
                    trades.append({'result': 'WIN',  'pnl': SL_PCT * RR * 100 - FEE_PCT * 100})
                    in_trade = False
            else:
                if high_c >= sl:
                    trades.append({'result': 'LOSS', 'pnl': -SL_PCT * 100 - FEE_PCT * 100})
                    in_trade = False
                elif low_c <= tp:
                    trades.append({'result': 'WIN',  'pnl': SL_PCT * RR * 100 - FEE_PCT * 100})
                    in_trade = False
            continue

        sig = get_signal(df, i, body_pct, vol_mult, brk_candles, use_trend)
        if sig is None:
            continue

        price     = df['close'].iloc[i]
        direction = sig
        entry     = price
        if direction == 'LONG':
            sl = price * (1 - SL_PCT)
            tp = price * (1 + SL_PCT * RR)
        else:
            sl = price * (1 + SL_PCT)
            tp = price * (1 - SL_PCT * RR)
        in_trade = True

    return trades

# ── Main ────────────────────────────────────────────────────────────────────
print('=' * 90)
print('BACKTEST 5M | SL=' + str(SL_PCT*100) + '% | R:R 1:' + str(RR) + ' | 180 giorni')
print('Confronto 5 combinazioni di parametri')
print('=' * 90)
print('{:<12} {:>8} {:>10} {:>10} {:>10} {:>10} {:>10}'.format(
    'Pair', 'A trades', 'A WR/PnL', 'B WR/PnL', 'C WR/PnL', 'D WR/PnL', 'E WR/PnL'))
print('-' * 90)

totals = {c['name']: {'trades': 0, 'wins': 0, 'pnl': 0.0} for c in COMBOS}

for pair in PAIRS:
    df = load_pair(pair, '5m')
    if df is None or len(df) < 500:
        print('{:<12} dati mancanti'.format(pair.split('/')[0]))
        continue

    combo_results = []
    for combo in COMBOS:
        trades = simulate(df, combo)
        if not trades:
            combo_results.append({'trades': 0, 'wr': 0, 'pnl': 0})
            continue
        wins = sum(1 for t in trades if t['result'] == 'WIN')
        pnl  = round(sum(t['pnl'] for t in trades), 1)
        wr   = round(wins / len(trades) * 100, 1)
        combo_results.append({'trades': len(trades), 'wr': wr, 'pnl': pnl})
        totals[combo['name']]['trades'] += len(trades)
        totals[combo['name']]['wins']   += wins
        totals[combo['name']]['pnl']    += pnl

    def fmt(r):
        tag = '+' if r['pnl'] >= 0 else ''
        return str(r['wr']) + '/' + tag + str(r['pnl']) + '%'

    print('{:<12} {:>8} {:>10} {:>10} {:>10} {:>10} {:>10}'.format(
        pair.split('/')[0],
        combo_results[0]['trades'],
        fmt(combo_results[0]),
        fmt(combo_results[1]),
        fmt(combo_results[2]),
        fmt(combo_results[3]),
        fmt(combo_results[4]),
    ))

print('=' * 90)
print()
print('RIEPILOGO TOTALE (WR% | PnL totale):')
for combo in COMBOS:
    t = totals[combo['name']]
    wr  = round(t['wins'] / t['trades'] * 100, 1) if t['trades'] > 0 else 0
    pnl = round(t['pnl'], 1)
    tag = ' ✓' if pnl > 0 else ' ✗'
    print('  ' + combo['name'] + ' | Trade: ' + str(t['trades']) +
          ' | WR: ' + str(wr) + '% | PnL: ' + str(pnl) + '%' + tag)

print()
print('Legenda: WR%/PnL% per ogni combo A-E')
print('Trend conferma = ultime 3 candele nella stessa direzione del segnale')
