# bybit-technical-bot

Automated trading bot for Bybit Perpetual Futures — **100% technical strategy, zero AI in decisions**.

[![Support on Ko-fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white)](https://ko-fi.com/marukoshi)
[![Follow on X](https://img.shields.io/badge/Follow-X-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/wowlookitsmark?s=21)
[![Bybit Referral](https://img.shields.io/badge/Trade_on-Bybit-F7A600?style=for-the-badge&logo=bybit&logoColor=white)](https://www.bybit.com/invite?ref=ZN6P37)

---

## What is this

A trading bot that operates on 6 assets in Bybit perpetual futures, making decisions based entirely on technical indicators: MACD, RSI, Bollinger Bands, EMA, volume and Fear & Greed Index. No AI calls, no external APIs for decisions — the live logic is identical to the backtest.

### Assets
`XAUTUSDT` `DOGEUSDT` `SUIUSDT` `XRPUSDT` `WLDUSDT` `ETHUSDT`

---

## Trading Logic

### Signal Score (0–7)
| Indicator | Points |
|---|---|
| MACD bullish/bearish cross | +2 |
| RSI < 32 or > 68 | +1 |
| Volume ratio > 1.8x | +1 |
| Bollinger Bands < 10% or > 90% | +1 |
| EMA trend + 4h trend aligned | +1 |
| F&G < 25 with bearish trend | +1 |

- **Score < 3** → Skip, no action
- **Score ≥ 3** → Trade opened according to parameters below

### SL/TP and sizing by score
| Score | Size | SL | TP | Ratio |
|---|---|---|---|---|
| 6–7 | 25% | 0.8% | 3.0% | 1:3.75 |
| 5 | 15% | 1.2% | 2.4% | 1:2 |
| 3–4 | 7% | 1.8% | 3.0% | 1:1.6 |

### Dynamic leverage (Fear & Greed)
| F&G | Max Leverage |
|---|---|
| < 25 (Extreme Fear) | 10x |
| 25–39 (Fear) | 20x |
| ≥ 40 | 50x |

### Daily filter
- **Bullish** (price > EMA20 > EMA50 daily) → Long only
- **Bearish** (price < EMA20 < EMA50 daily) → Short only
- **Neutral** → Follows 15m technical signal

### Progressive trailing stop (v1.3)

A dedicated thread runs every **30 seconds** and manages all open positions independently from the signal loop. Stop loss moves progressively as the trade advances toward TP, locking in guaranteed profit at each stage. Break-even includes fees (0.11% round trip).

| Progress toward TP | SL moves to | Guaranteed gain |
|---|---|---|
| 20% | Break-even + fees | ~0% (no loss) |
| 35% | Entry + 15% of TP distance | +15% of TP dist |
| 50% | Entry + 30% of TP distance | +30% of TP dist |
| 65% | Entry + 45% of TP distance | +45% of TP dist |
| 80% | Entry + 60% of TP distance | +60% of TP dist |
| 90% | Entry + 75% of TP distance | +75% of TP dist |

**Example** — Score 6, entry $100, TP $103 (+3%), SL $99.20 (-0.8%):

| Progress | Price | SL moves to | If stopped here |
|---|---|---|---|
| 20% | $100.60 | $100.11 | ~$0 |
| 35% | $101.05 | $100.45 | +0.45% |
| 50% | $101.50 | $100.90 | +0.90% |
| 65% | $101.95 | $101.35 | +1.35% |
| 80% | $102.40 | $101.80 | +1.80% |
| 90% | $102.70 | $102.10 | +2.10% |
| TP hit | $103.00 | — | +3.00% |

**Safety checks applied on every update:**
- SL only moves in the correct direction (never widens the loss)
- SL never placed above/below the current mark price
- Tick size respected per asset (prevents Bybit `price filter` rejection)
- Thread-safe: signal loop and trailing thread use per-symbol locks
- State persisted to `position_state.json` — survives bot restarts
- On restart: state reconciled with live Bybit positions, correct scaglione restored automatically

### Circuit breaker
Daily loss > 8% → bot pauses automatically for 24 hours.

---

## Architecture (v1.3)

```
Thread 1 — Signal loop (every 15 minutes)
  → Fetch Fear & Greed
  → For each asset: fetch candles, compute indicators, score signal
  → Apply daily trend filter
  → Open new positions if score ≥ 3 and direction confirmed

Thread 2 — Trailing loop (every 30 seconds)
  → Fetch all open positions from Bybit
  → For each: compute progress toward TP
  → Move SL to next scaglione if threshold crossed
  → Notify via Telegram on every SL update
```

---

## Backtest Results (180 days, $780 balance, fees included)

Tested on 24 assets. Fee: 0.055% taker per leg (0.11% round trip). MIN_VALUE $150.

### Selected assets (top 6)
| Asset | Trades | Win Rate | PnL |
|---|---|---|---|
| DOGE/USDT | 453 | 52.5% | +70.0% |
| SUI/USDT | 503 | 51.7% | +49.5% |
| XRP/USDT | 366 | 52.2% | +45.3% |
| XAUT/USDT | 105 | 57.1% | +34.0% |
| WLD/USDT | 535 | 50.5% | +31.1% |
| ETH/USDT | 367 | 49.9% | +30.0% |
| **TOP 6 TOTAL** | **2329** | **51.6%** | **+259.9%** |

### Excluded assets (negative performance)
| Asset | Trades | Win Rate | PnL |
|---|---|---|---|
| LINK/USDT | 412 | 46.1% | -66.0% |
| BCH/USDT | 287 | 47.4% | -29.7% |
| ATOM/USDT | 334 | 47.0% | -28.1% |
| LTC/USDT | 298 | 48.0% | -12.1% |
| DOT/USDT | 321 | 47.7% | -11.1% |
| ADA/USDT | 445 | 48.3% | -8.4% |

> Asset selection is critical. The same strategy produces +259.9% on the top 6 and negative results on the rest. Always backtest before adding new pairs.

---

## Requirements

```bash
pip install pandas ta pybit python-dotenv requests
```

### `.env` file
```
BYBIT_API_KEY=xxx
BYBIT_API_SECRET=xxx
TELEGRAM_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
```

---

## Setup & Run

### Local
```bash
python3 bot.py
```

### VPS — recommended for 24/7 uptime
Tested on **Hetzner VPS Ubuntu 22.04**.

```bash
# First install
python3 -m venv venv
source venv/bin/activate
pip install pandas ta pybit python-dotenv requests

# Start
nohup python3 bot.py > nohup.out 2>&1 &

# Monitor logs
tail -f nohup.out

# Stop
pkill -f bot.py

# Restart
pkill -f bot.py && sleep 2 && nohup python3 bot.py > nohup.out 2>&1 &

# Check if running
pgrep -a python3
```

### Update bot from repo (VPS)
```bash
cd ~/trading_bot && source venv/bin/activate
curl -H "Authorization: token YOUR_TOKEN" \
  -L "https://raw.githubusercontent.com/MarkusSela/bybit-technical-bot/main/bot.py" \
  -o bot.py
pkill -f bot.py && sleep 2 && nohup python3 bot.py > nohup.out 2>&1 &
```

---

## Backtest

```bash
freqtrade download-data --exchange bybit \
  --pairs DOGE/USDT SUI/USDT XRP/USDT XAUT/USDT WLD/USDT ETH/USDT \
  --timeframe 15m 1d --days 180

python3 backtest_full.py
```

---

## Repository structure

```
bot.py                  # Live bot v1.3
backtest_full.py        # 24-asset backtest
position_state.json     # Auto-generated — trailing state (do not edit manually)
README.md               # This file
```

---

## Changelog

| Version | Changes |
|---|---|
| v1.0 | Pure technical bot. Same logic as backtest. No AI. |
| v1.1 | Updated assets with top 6 from 24-asset backtest (XRP, WLD replace BTC, SOL). MIN_VALUE $150. |
| v1.2 | Loop 15min. TP score 3 at 3%. Floating point qty fix. XRP MIN_QTY fix. |
| v1.3 | Progressive trailing stop thread (30s). 6 scaglioni at 20/35/50/65/80/90% of TP distance. Break-even includes fees. State persistence across restarts. Thread-safe per-symbol locks. Tick size rounding per asset. |

---

## Disclaimer

This bot is for educational purposes. Trading perpetual futures involves significant risk of loss. Past backtest performance does not guarantee future results. Use at your own risk.
