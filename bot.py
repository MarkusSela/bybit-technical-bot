import os, time, json, logging, requests, math, threading
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import ta
from pybit.unified_trading import HTTP

load_dotenv()

BYBIT_API_KEY      = os.getenv('BYBIT_API_KEY')
BYBIT_API_SECRET   = os.getenv('BYBIT_API_SECRET')
TELEGRAM_TOKEN     = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')

MAX_LEVERAGE       = 50
MIN_LEVERAGE       = 5
RISK_MIN_PCT       = 0.05
RISK_MAX_PCT       = 0.25
MAX_DAILY_LOSS_PCT = 0.08
MAX_OPEN_POSITIONS = 6
LOOP_INTERVAL_SEC  = 900
TRAILING_INTERVAL  = 30
TIMEFRAME          = '15'
CANDLES_LOOKBACK   = 100
MIN_VALUE          = 150.0
FEE_PCT            = 0.0011   # 0.11% round trip per break-even reale

STATE_FILE         = 'position_state.json'

# ── Scaglioni trailing progressivo ────────────────────────────────────────
# (soglia_progresso_verso_TP, frazione_distanza_entry_TP_bloccata_come_gain)
# Scaglione 0 (20%): break-even + fee — nessuna perdita da qui in poi
# Scaglioni successivi: quota crescente della distanza entry→TP garantita
SCAGLIONI = [
    (0.20, 0.00),   # 20% del tragitto → SL a break-even + fee
    (0.35, 0.15),   # 35%              → SL a entry + 15% della distanza TP
    (0.50, 0.30),   # 50%              → SL a entry + 30%
    (0.65, 0.45),   # 65%              → SL a entry + 45%
    (0.80, 0.60),   # 80%              → SL a entry + 60%
    (0.90, 0.75),   # 90%              → SL a entry + 75%
]

SCORE_PARAMS = {
    6: {'sl': 0.008, 'tp': 0.030, 'risk_pct': 0.25},
    5: {'sl': 0.012, 'tp': 0.024, 'risk_pct': 0.15},
    3: {'sl': 0.018, 'tp': 0.030, 'risk_pct': 0.07},
}

TRAILING_STEPS = {
    'XAUTUSDT': 0.003,
    'DOGEUSDT': 0.012,
    'SUIUSDT':  0.012,
    'XRPUSDT':  0.010,
    'WLDUSDT':  0.015,
    'ETHUSDT':  0.006,
}
MIN_QTY = {
    'XAUTUSDT': 0.01,
    'DOGEUSDT': 1.0,
    'SUIUSDT':  1.0,
    'XRPUSDT':  10.0,
    'WLDUSDT':  1.0,
    'ETHUSDT':  0.01,
}

BASE_SYMBOLS = ['XAUTUSDT', 'DOGEUSDT', 'SUIUSDT', 'XRPUSDT', 'WLDUSDT', 'ETHUSDT']

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Fonti dati ────────────────────────────────────────────────────────────
# Prezzi e candele: Bybit API — 15m e daily
# Fear & Greed: alternative.me/fng
# Notifiche: Telegram Bot API
# Trailing progressivo: thread interno ogni 30 secondi
# Nessuna chiamata AI — decisioni 100% tecniche
# ──────────────────────────────────────────────────────────────────────────

# ── State management ──────────────────────────────────────────────────────
# position_state: per ogni simbolo conserva entry, tp_price, sl_price, side, last_scaglione
# Persistito su disco — sopravvive al riavvio del bot
position_state = {}
state_lock     = threading.Lock()
symbol_locks   = {s: threading.Lock() for s in BASE_SYMBOLS}

def load_position_state():
    global position_state
    try:
        if Path(STATE_FILE).exists():
            with open(STATE_FILE, 'r') as f:
                position_state = json.load(f)
            log.info('[STATE] Caricato: ' + str(list(position_state.keys())))
    except Exception as e:
        log.warning('[STATE] Load error: ' + str(e))
        position_state = {}

def save_position_state():
    try:
        with state_lock:
            with open(STATE_FILE, 'w') as f:
                json.dump(position_state, f, indent=2)
    except Exception as e:
        log.warning('[STATE] Save error: ' + str(e))

def sync_state_with_bybit(client):
    """
    Al riavvio riconcilia lo state con le posizioni reali su Bybit.
    Per ogni posizione aperta non presente nello state, la ricostruisce
    usando entry_price e stimando il TP dalla distanza SL (ratio 1:2.5).
    Salta automaticamente agli scaglioni gia raggiunti.
    """
    try:
        pos_r = api_call_with_retry(lambda: client.get_positions(
            category='linear', settleCoin='USDT'))
        open_symbols = set()

        for p in pos_r['result']['list']:
            size = float(p['size'])
            if size == 0:
                continue
            symbol = p['symbol']
            open_symbols.add(symbol)

            with state_lock:
                if symbol not in position_state:
                    entry  = float(p['avgPrice'])
                    side   = p['side']
                    sl_val = float(p.get('stopLoss', 0))

                    if sl_val > 0 and entry > 0:
                        sl_dist  = abs(entry - sl_val) / entry
                        tp_dist  = sl_dist * 2.5
                        tp_price = entry * (1 + tp_dist) if side == 'Buy' else entry * (1 - tp_dist)
                    else:
                        tp_price = entry * 1.03 if side == 'Buy' else entry * 0.97

                    # Determina ultimo scaglione gia raggiunto
                    mark = float(p.get('markPrice', entry))
                    tp_d = abs(tp_price - entry)
                    progress = ((mark - entry) / tp_d) if side == 'Buy' else ((entry - mark) / tp_d)
                    last_scag = -1
                    if progress > 0:
                        for i, (soglia, _) in enumerate(SCAGLIONI):
                            if progress >= soglia:
                                last_scag = i

                    position_state[symbol] = {
                        'entry':          entry,
                        'tp_price':       round(tp_price, 6),
                        'sl_price':       sl_val,
                        'side':           side,
                        'last_scaglione': last_scag,
                    }
                    log.info('[STATE] Ricostruito: ' + symbol +
                             ' entry=' + str(entry) + ' side=' + side +
                             ' tp_stimato=' + str(round(tp_price, 6)) +
                             ' last_scaglione=' + str(last_scag))

        # Rimuove simboli non piu aperti
        for s in list(position_state.keys()):
            if s not in open_symbols:
                del position_state[s]
                log.info('[STATE] Rimosso (chiuso al riavvio): ' + s)

        save_position_state()

    except Exception as e:
        log.warning('[STATE] Sync error: ' + str(e))

# ── Telegram ───────────────────────────────────────────────────────────────
def tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=5
        )
    except Exception as e:
        log.warning('Telegram error: ' + str(e))

def tg_startup(balance):
    tg(
        '<b>Technical Trading Bot v1.3 avviato</b>' + chr(10) +
        'Balance: <b>$' + str(balance) + ' USDT</b>' + chr(10) +
        'Asset: <b>' + ', '.join(BASE_SYMBOLS) + '</b>' + chr(10) +
        'Loop segnali: 15 min | Trailing: 30 sec | Filtro daily: ON' + chr(10) +
        'Scaglioni trailing: 20/35/50/65/80/90% | Break-even con fee incluse' + chr(10) +
        'Score minimo: 3/7 | MIN_VALUE: $' + str(int(MIN_VALUE))
    )

def tg_trade(symbol, action, price, sl, tp, score, daily_trend, params):
    nl = chr(10)
    risk_pct = int(params['risk_pct'] * 100)
    lines = ['<b>' + action + ' -- ' + symbol + '</b> (score ' + str(score) + '/7)']
    lines.append('Entrata: <b>$' + str(round(price, 6)) + '</b> | Size: <b>' + str(risk_pct) + '%</b>')
    lines.append('SL: $' + str(round(sl, 6)) + ' | TP: $' + str(round(tp, 6)))
    lines.append('Daily: ' + daily_trend)
    tg(nl.join(lines))

def tg_scaglione(symbol, scaglione_idx, old_sl, new_sl, mark, progress_pct, guaranteed_pct):
    nl = chr(10)
    tg(
        'TRAILING SCAGLIONE ' + str(scaglione_idx + 1) + '/6 — <b>' + symbol + '</b>' + nl +
        'Progresso TP: <b>' + str(round(progress_pct * 100, 1)) + '%</b>' + nl +
        'SL: $' + str(round(old_sl, 6)) + ' → <b>$' + str(round(new_sl, 6)) + '</b>' + nl +
        'Mark: $' + str(round(mark, 6)) + nl +
        'Gain garantito: <b>+' + str(round(guaranteed_pct * 100, 2)) + '%</b>'
    )

def tg_skip_summary(skips):
    if not skips:
        return
    nl = chr(10)
    lines = ['<b>Ciclo completato — nessuna azione:</b>', '']
    for symbol, reason in skips.items():
        lines.append('<b>' + symbol + '</b> — <i>' + str(reason)[:80] + '</i>')
    tg(nl.join(lines))

def tg_circuit_breaker(loss_pct):
    tg('<b>CIRCUIT BREAKER</b>' + chr(10) +
       'Perdita giornaliera: <b>' + str(round(loss_pct * 100, 2)) + '%</b> — bot in pausa 24h')

def tg_error(msg):
    tg('<b>Errore</b>' + chr(10) + msg[:200])

# ── Bybit ──────────────────────────────────────────────────────────────────
def get_bybit_client():
    return HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)

def api_call_with_retry(func, max_retries=3, base_delay=1.0):
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))
    raise Exception('Max retries exceeded')

def get_tick_size(client, symbol):
    try:
        info = client.get_instruments_info(category='linear', symbol=symbol)
        return float(info['result']['list'][0]['priceFilter']['tickSize'])
    except:
        return 0.0001

def round_to_tick(price, tick_size):
    if tick_size <= 0:
        return price
    decimals = len(str(tick_size).rstrip('0').split('.')[-1]) if '.' in str(tick_size) else 0
    return round(round(price / tick_size) * tick_size, decimals)

def fetch_candles(client, symbol, interval, limit):
    resp = api_call_with_retry(lambda: client.get_kline(
        category='linear', symbol=symbol, interval=interval, limit=limit
    ))
    raw = resp['result']['list']
    df  = pd.DataFrame(raw, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df  = df.iloc[::-1].reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    return df

def get_daily_trend(client, symbol):
    try:
        df    = fetch_candles(client, symbol, 'D', 60)
        if len(df) < 20:
            return 'neutral'
        close = df['close']
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

def compute_indicators(df):
    close     = df['close']
    high      = df['high']
    low       = df['low']
    vol       = df['volume']
    rsi       = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    macd      = ta.trend.MACD(close)
    bb        = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper  = bb.bollinger_hband().iloc[-1]
    bb_lower  = bb.bollinger_lband().iloc[-1]
    bb_pct    = (close.iloc[-1] - bb_lower) / (bb_upper - bb_lower + 1e-9)
    ema20     = ta.trend.EMAIndicator(close, window=20).ema_indicator().iloc[-1]
    ema50     = ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1]
    atr       = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1]
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
        'price':        round(close.iloc[-1], 6),
        'rsi_14':       round(rsi, 2),
        'macd_hist':    round(macd_hist, 6),
        'macd_cross':   macd_cross,
        'bb_pct':       round(bb_pct, 3),
        'ema_trend':    'bullish' if ema20 > ema50 else 'bearish',
        'volume_ratio': round(vol_ratio, 2),
        'atr_pct':      round(atr / close.iloc[-1] * 100, 3),
        'chg_4h_pct':   chg_4h,
        'trend_4h':     trend_4h,
        'trend_bias':   trend_bias,
    }

def signal_score(indicators, fg_value):
    score = 0
    if indicators['rsi_14'] < 32 or indicators['rsi_14'] > 68:
        score += 1
    if indicators['macd_cross'] in ('bullish', 'bearish'):
        score += 2
    if indicators['volume_ratio'] > 1.8:
        score += 1
    if indicators['bb_pct'] < 0.1 or indicators['bb_pct'] > 0.9:
        score += 1
    if indicators['ema_trend'] == 'bearish' and indicators['trend_4h'] == 'bearish':
        score += 1
    elif indicators['ema_trend'] == 'bullish' and indicators['trend_4h'] == 'bullish':
        score += 1
    if fg_value < 25 and indicators['trend_bias'] in ('bear', 'strong_bear'):
        score += 1
    return score

def get_direction(indicators):
    if indicators['trend_4h'] == 'bullish' and indicators['ema_trend'] == 'bullish':
        return 'LONG'
    elif indicators['trend_4h'] == 'bearish' and indicators['ema_trend'] == 'bearish':
        return 'SHORT'
    return None

# ── Account ────────────────────────────────────────────────────────────────
def get_account(client):
    r       = api_call_with_retry(lambda: client.get_wallet_balance(accountType='UNIFIED'))
    balance = float(r['result']['list'][0]['totalWalletBalance'])
    pos_r   = api_call_with_retry(lambda: client.get_positions(category='linear', settleCoin='USDT'))
    positions = []
    for p in pos_r['result']['list']:
        size = float(p['size'])
        if size == 0:
            continue
        positions.append({
            'symbol':         p['symbol'],
            'side':           p['side'],
            'size':           size,
            'entry_price':    float(p['avgPrice']),
            'unrealized_pnl': float(p['unrealisedPnl']),
            'leverage':       int(float(p['leverage'])),
            'mark_price':     float(p.get('markPrice', p['avgPrice'])),
            'stop_loss':      float(p.get('stopLoss', 0)),
        })
    return {'balance': round(balance, 2), 'positions': positions}

def fetch_fear_greed():
    try:
        r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        d = r.json()['data'][0]
        return {'value': int(d['value']), 'label': d['value_classification']}
    except:
        return {'value': 50, 'label': 'Neutral'}

def get_funding_rate(client, symbol):
    try:
        r = client.get_funding_rate_history(category='linear', symbol=symbol, limit=1)
        return float(r['result']['list'][0]['fundingRate'])
    except:
        return 0.0

def get_max_leverage(fg_value):
    if fg_value < 25:
        return 10
    elif fg_value < 40:
        return 20
    else:
        return 50

# ── Trailing progressivo ───────────────────────────────────────────────────
def compute_new_sl(entry, tp_price, side, scaglione_idx):
    """
    Calcola il nuovo SL per lo scaglione dato.
    Scaglione 0: break-even + fee (FEE_PCT sull'entry)
    Scaglioni 1-5: entry + frazione della distanza entry→TP
    """
    soglia, frazione = SCAGLIONI[scaglione_idx]
    tp_dist    = abs(tp_price - entry)
    be_offset  = entry * FEE_PCT

    if frazione == 0.0:
        # Scaglione 0: break-even + fee
        return (entry + be_offset) if side == 'Buy' else (entry - be_offset)
    else:
        gain = tp_dist * frazione
        return (entry + gain) if side == 'Buy' else (entry - gain)

def trailing_loop(client):
    """
    Thread separato — gira ogni 30 secondi.
    Per ogni posizione aperta:
      1. Calcola il progresso verso il TP
      2. Identifica il nuovo scaglione raggiunto
      3. Calcola il nuovo SL
      4. Verifica safety (SL nella direzione corretta, non sopra il mark)
      5. Aggiorna via API e notifica su Telegram
    Gestisce tutte le posizioni in sequenza ogni ciclo.
    """
    log.info('[TRAILING] Thread avviato — intervallo ' + str(TRAILING_INTERVAL) + 's')
    tick_cache = {}

    while True:
        try:
            pos_r = api_call_with_retry(lambda: client.get_positions(
                category='linear', settleCoin='USDT'))

            for p in pos_r['result']['list']:
                size = float(p['size'])
                if size == 0:
                    continue

                symbol = p['symbol']
                if symbol not in BASE_SYMBOLS:
                    continue

                mark = float(p.get('markPrice', p['avgPrice']))
                side = p['side']

                if symbol not in tick_cache:
                    tick_cache[symbol] = get_tick_size(client, symbol)
                tick = tick_cache[symbol]

                with symbol_locks.get(symbol, threading.Lock()):
                    with state_lock:
                        state = position_state.get(symbol)
                    if state is None:
                        continue

                    entry      = state['entry']
                    tp_price   = state['tp_price']
                    current_sl = state['sl_price']
                    last_scag  = state.get('last_scaglione', -1)

                    tp_dist = abs(tp_price - entry)
                    if tp_dist == 0:
                        continue

                    progress = ((mark - entry) / tp_dist) if side == 'Buy' else ((entry - mark) / tp_dist)
                    if progress <= 0:
                        continue

                    # Trova lo scaglione piu alto raggiunto
                    target_scag = -1
                    for i, (soglia, _) in enumerate(SCAGLIONI):
                        if progress >= soglia:
                            target_scag = i

                    if target_scag <= last_scag:
                        continue

                    new_sl_raw = compute_new_sl(entry, tp_price, side, target_scag)
                    new_sl     = round_to_tick(new_sl_raw, tick)

                    # Safety checks
                    if side == 'Buy' and new_sl <= current_sl:
                        continue
                    if side == 'Sell' and new_sl >= current_sl:
                        continue
                    if side == 'Buy' and new_sl >= mark:
                        continue
                    if side == 'Sell' and new_sl <= mark:
                        continue

                    try:
                        api_call_with_retry(lambda: client.set_trading_stop(
                            category='linear',
                            symbol=symbol,
                            stopLoss=str(new_sl),
                            slTriggerBy='MarkPrice'
                        ))

                        _, frazione = SCAGLIONI[target_scag]
                        guaranteed_pct = frazione if frazione > 0 else FEE_PCT

                        tg_scaglione(symbol, target_scag, current_sl, new_sl,
                                     mark, progress, guaranteed_pct)
                        log.info('[TRAILING] ' + symbol +
                                 ' scaglione ' + str(target_scag + 1) + '/6' +
                                 ' progress=' + str(round(progress * 100, 1)) + '%' +
                                 ' SL ' + str(current_sl) + ' -> ' + str(new_sl))

                        with state_lock:
                            position_state[symbol]['sl_price']       = new_sl
                            position_state[symbol]['last_scaglione'] = target_scag
                        save_position_state()

                    except Exception as e:
                        log.warning('[TRAILING] ' + symbol + ' error: ' + str(e))

        except Exception as e:
            log.error('[TRAILING] Loop error: ' + str(e))

        time.sleep(TRAILING_INTERVAL)

# ── Qty ────────────────────────────────────────────────────────────────────
def get_qty_step(client, symbol):
    try:
        info = client.get_instruments_info(category='linear', symbol=symbol)
        return float(info['result']['list'][0]['lotSizeFilter']['qtyStep'])
    except:
        return 0.001

def calculate_qty(balance, price, leverage, risk_pct, step, symbol):
    if price <= 0 or step <= 0:
        return 0
    qty      = (balance * risk_pct * leverage) / price
    min_qty  = MIN_QTY.get(symbol, step)
    decimals = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
    qty_floored = round(math.floor(round(qty / step, 8)) * step, decimals)
    return max(qty_floored, min_qty)

def get_params(score):
    if score >= 6:
        return SCORE_PARAMS[6]
    elif score >= 5:
        return SCORE_PARAMS[5]
    else:
        return SCORE_PARAMS[3]

# ── Execute ────────────────────────────────────────────────────────────────
def execute_trade(client, symbol, direction, score, daily_trend, account, fg_value):
    params   = get_params(score)
    sl_pct   = params['sl']
    tp_pct   = params['tp']
    risk_pct = params['risk_pct']

    max_lev  = get_max_leverage(fg_value)
    if score >= 6:
        leverage = max_lev
    elif score >= 5:
        leverage = max(MIN_LEVERAGE, max_lev // 2)
    else:
        leverage = MIN_LEVERAGE

    if leverage >= 20 and sl_pct > 0.003:
        sl_pct = 0.003

    open_pos = next((p for p in account['positions'] if p['symbol'] == symbol), None)
    if open_pos:
        log.info('[' + symbol + '] Posizione gia aperta, HOLD')
        return 'HOLD'

    if len(account['positions']) >= MAX_OPEN_POSITIONS:
        log.info('[' + symbol + '] Max posizioni raggiunte')
        return 'MAX_POS'

    funding = get_funding_rate(client, symbol)
    if direction == 'LONG' and funding > 0.0003:
        log.info('[' + symbol + '] Funding troppo alto per LONG')
        return 'FUNDING'
    if direction == 'SHORT' and funding < -0.0003:
        log.info('[' + symbol + '] Funding negativo: squeeze risk SHORT')
        return 'FUNDING'

    try:
        ticker = api_call_with_retry(lambda: client.get_tickers(category='linear', symbol=symbol))
        price  = float(ticker['result']['list'][0]['lastPrice'])
    except Exception as e:
        log.warning('[' + symbol + '] Prezzo non disponibile: ' + str(e))
        return 'ERROR'

    sl_price = round(price * (1 - sl_pct) if direction == 'LONG' else price * (1 + sl_pct), 6)
    tp_price = round(price * (1 + tp_pct) if direction == 'LONG' else price * (1 - tp_pct), 6)

    try:
        client.set_margin_mode(setMarginMode='ISOLATED_MARGIN')
    except:
        pass
    try:
        client.switch_position_mode(category='linear', symbol=symbol, mode=0)
    except:
        pass
    try:
        client.set_leverage(category='linear', symbol=symbol,
                            buyLeverage=str(leverage), sellLeverage=str(leverage))
    except:
        pass

    step = get_qty_step(client, symbol)
    qty  = calculate_qty(account['balance'], price, leverage, risk_pct, step, symbol)

    if qty <= 0:
        return 'QTY_ZERO'

    trade_value = qty * price
    if trade_value < MIN_VALUE:
        log.info('[' + symbol + '] Controvalore $' + str(round(trade_value, 0)) +
                 ' sotto minimo $' + str(int(MIN_VALUE)))
        return 'MIN_VALUE'

    side = 'Buy' if direction == 'LONG' else 'Sell'
    try:
        client.place_order(
            category='linear', symbol=symbol, side=side,
            orderType='Market', qty=str(qty),
            stopLoss=str(sl_price), takeProfit=str(tp_price),
            slTriggerBy='MarkPrice', tpTriggerBy='MarkPrice'
        )

        with symbol_locks.get(symbol, threading.Lock()):
            with state_lock:
                position_state[symbol] = {
                    'entry':          price,
                    'tp_price':       tp_price,
                    'sl_price':       sl_price,
                    'side':           side,
                    'last_scaglione': -1,
                }
            save_position_state()

        tg_trade(symbol, direction, price, sl_price, tp_price, score, daily_trend, params)
        log.info('[' + symbol + '] ' + direction +
                 ' score=' + str(score) + ' daily=' + daily_trend +
                 ' leva=' + str(leverage) + 'x @ ' + str(price) +
                 ' SL=' + str(sl_price) + ' TP=' + str(tp_price))
        return 'OK'

    except Exception as e:
        log.error('[' + symbol + '] Errore ordine: ' + str(e))
        tg_error('Ordine fallito ' + symbol + ': ' + str(e)[:100])
        return 'ERROR'

# ── Cleanup state ──────────────────────────────────────────────────────────
def cleanup_state(active_symbols):
    with state_lock:
        for s in list(position_state.keys()):
            if s not in active_symbols:
                del position_state[s]
                log.info('[STATE] Rimosso (chiuso): ' + s)
    save_position_state()

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    log.info('=== Technical Trading Bot v1.3 avviato ===')
    client = get_bybit_client()

    load_position_state()
    sync_state_with_bybit(client)

    account = get_account(client)
    tg_startup(account['balance'])

    t = threading.Thread(target=trailing_loop, args=(client,), daemon=True)
    t.start()

    daily_start_balance  = account['balance']
    weekly_start_balance = account['balance']
    last_daily_reset     = datetime.now()
    last_weekly_reset    = datetime.now()

    while True:
        try:
            if (datetime.now() - last_daily_reset).total_seconds() > 86400:
                daily_start_balance = account['balance']
                last_daily_reset    = datetime.now()
            if (datetime.now() - last_weekly_reset).total_seconds() > 604800:
                weekly_start_balance = account['balance']
                last_weekly_reset    = datetime.now()

            account = get_account(client)
            pnl_pct = (account['balance'] - daily_start_balance) / daily_start_balance
            if pnl_pct < -MAX_DAILY_LOSS_PCT:
                tg_circuit_breaker(pnl_pct)
                time.sleep(86400)
                daily_start_balance = account['balance']
                last_daily_reset    = datetime.now()
                continue

            fg       = fetch_fear_greed()
            pnl_oggi = round(pnl_pct * 100, 2)
            pnl_sett = round((account['balance'] - weekly_start_balance) / weekly_start_balance * 100, 2)

            log.info('F&G: ' + str(fg['value']) + ' (' + fg['label'] + ')')
            log.info('PnL oggi: ' + str(pnl_oggi) + '% | Settimana: ' + str(pnl_sett) + '%')
            log.info('Balance: $' + str(account['balance']) + ' | Pos: ' + str(len(account['positions'])))

            active = [p['symbol'] for p in account['positions']]
            cleanup_state(active)

            skips = {}
            for symbol in BASE_SYMBOLS:
                try:
                    daily_trend = get_daily_trend(client, symbol)
                    df          = fetch_candles(client, symbol, TIMEFRAME, CANDLES_LOOKBACK)
                    indicators  = compute_indicators(df)
                    score       = signal_score(indicators, fg['value'])

                    log.info('[' + symbol + '] daily=' + daily_trend +
                             ' score=' + str(score) + ' rsi=' + str(indicators['rsi_14']))

                    if score < 3:
                        skips[symbol] = 'Score ' + str(score) + '/7 insufficiente'
                        continue

                    direction = get_direction(indicators)
                    if direction is None:
                        skips[symbol] = 'Direzione ambigua (EMA e 4h in contrasto)'
                        continue

                    if direction == 'LONG' and daily_trend == 'bearish':
                        skips[symbol] = 'LONG bloccato: trend daily bearish'
                        continue
                    if direction == 'SHORT' and daily_trend == 'bullish':
                        skips[symbol] = 'SHORT bloccato: trend daily bullish'
                        continue

                    result = execute_trade(client, symbol, direction, score,
                                           daily_trend, account, fg['value'])
                    if result != 'OK':
                        skips[symbol] = result

                    time.sleep(2)

                except Exception as e:
                    log.error('[' + symbol + '] Errore: ' + str(e))
                    continue

            tg_skip_summary(skips)

        except Exception as e:
            log.error('Errore loop: ' + str(e))
            tg_error(str(e)[:150])

        log.info('Prossimo ciclo in ' + str(LOOP_INTERVAL_SEC // 60) + ' minuti' + chr(10))
        time.sleep(LOOP_INTERVAL_SEC)

if __name__ == '__main__':
    main()
