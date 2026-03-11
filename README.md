# Technical Trading Bot v1.2

Bot di trading automatico su Bybit Perpetual Futures — **100% tecnico, zero AI nelle decisioni**.

[![Sostenimi su Ko-fi](https://img.shields.io/badge/Sostenimi-Ko--fi-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white)](https://ko-fi.com/marukoshi)
[![Seguimi su X](https://img.shields.io/badge/Seguimi-X-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/wowlookitsmark?s=21)

---

## Cos'è

Bot che opera su 6 asset in perpetual futures Bybit, prendendo decisioni basate esclusivamente su indicatori tecnici: MACD, RSI, Bollinger Bands, EMA, volume e Fear & Greed Index. Nessuna chiamata a modelli AI — la logica è identica al backtest, zero discrepanze.

### Asset
`XAUTUSDT` `DOGEUSDT` `SUIUSDT` `XRPUSDT` `WLDUSDT` `ETHUSDT`

---

## Logica di trading

### Signal Score (0–7)
| Indicatore | Punti |
|---|---|
| MACD cross bullish/bearish | +2 |
| RSI < 32 o > 68 | +1 |
| Volume ratio > 1.8x | +1 |
| Bollinger Bands < 10% o > 90% | +1 |
| EMA trend + trend 4h concordi | +1 |
| F&G < 25 con trend bearish | +1 |

- **Score < 3** → SKIP diretto, nessuna azione
- **Score ≥ 3** → trade aperto secondo i parametri sotto

### SL/TP e sizing per score
| Score | Size | SL | TP | Ratio |
|---|---|---|---|---|
| 6–7 | 25% | 0.8% | 3.0% | 1:3.75 |
| 5 | 15% | 1.2% | 2.4% | 1:2 |
| 3–4 | 7% | 1.8% | 3.0% | 1:1.6 |

### Leva dinamica (Fear & Greed)
| F&G | Leva max |
|---|---|
| < 25 (Extreme Fear) | 10x |
| 25–39 (Fear) | 20x |
| ≥ 40 | 50x |

### Filtro daily
- **Bullish** (prezzo > EMA20 > EMA50 giornaliero) → solo LONG
- **Bearish** (prezzo < EMA20 < EMA50 giornaliero) → solo SHORT
- **Neutral** → segue il segnale tecnico 15m

### Trailing stop
Il bot aggiorna automaticamente lo SL seguendo il prezzo a favore, asset per asset:

| Asset | Step trailing |
|---|---|
| XAUT | 0.3% |
| ETH | 0.6% |
| XRP | 1.0% |
| DOGE | 1.2% |
| SUI | 1.2% |
| WLD | 1.5% |

### Circuit breaker
Perdita giornaliera > 8% → bot in pausa 24h automatica.

---

## Backtest (180 giorni, $780 balance, fee incluse)

Testato su 24 asset, selezionati i top 6 per performance:

| Asset | Trade | Win Rate | PnL |
|---|---|---|---|
| DOGE/USDT | 453 | 52.5% | +70.0% |
| SUI/USDT | 503 | 51.7% | +49.5% |
| XRP/USDT | 366 | 52.2% | +45.3% |
| XAUT/USDT | 105 | 57.1% | +34.0% |
| WLD/USDT | 535 | 50.5% | +31.1% |
| ETH/USDT | 367 | 49.9% | +30.0% |
| **TOP 6** | **2329** | **51.6%** | **+259.9%** |

> Fee incluse: 0.055% taker per leg (0.11% round trip). MIN_VALUE $150.

---

## Risultati live

| Data inizio | Balance iniziale | Balance attuale |
|---|---|---|
| 10 Mar 2026 | $774 | — |

*(aggiornato manualmente)*

---

## Requisiti

```bash
pip install pandas ta pybit python-dotenv httpx requests
```

### File `.env`
```
BYBIT_API_KEY=xxx
BYBIT_API_SECRET=xxx
TELEGRAM_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
```

---

## Avvio

### In locale
```bash
python3 bot.py
```

### Su VPS (consigliato — bot attivo 24/7)
Setup testato su **Hetzner VPS Ubuntu 22.04**.

```bash
# Prima installazione
python3 -m venv venv
source venv/bin/activate
pip install pandas ta pybit python-dotenv httpx requests

# Avvio in background
nohup python3 bot.py > nohup.out 2>&1 &

# Monitoraggio log
tail -f nohup.out

# Stop
pkill -f bot.py
```

### Aggiornare bot da questo repo (VPS)
```bash
cd ~/trading_bot && source venv/bin/activate
curl -H "Authorization: token TUO_TOKEN" \
  -L "https://raw.githubusercontent.com/MarkusSela/technical_trading_bot/main/bot.py" \
  -o bot.py
nohup python3 bot.py > nohup.out 2>&1 &
```

---

## Backtest

```bash
# Scarica dati storici con Freqtrade
freqtrade download-data --exchange bybit \
  --pairs DOGE/USDT SUI/USDT XRP/USDT XAUT/USDT WLD/USDT ETH/USDT \
  --timeframe 15m 1d --days 180

# Esegui backtest
python3 backtest_full.py
```

---

## Struttura repo

```
bot.py              # Bot live v1.2
backtest_full.py    # Backtest 24 asset
README.md           # Questo file
```
