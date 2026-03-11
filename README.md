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

### Trailing stop
The bot automatically updates the SL following the price in profit direction:

| Asset | Trailing step |
|---|---|
| XAUT | 0.3% |
| ETH | 0.6% |
| XRP | 1.0% |
| DOGE | 1.2% |
| SUI | 1.2% |
| WLD | 1.5% |

### Circuit breaker
Daily loss > 8% → bot pauses automatically for 24 hours.

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
| BNB/USDT | 381 | 47.9% | -7.2% |
| AVAX/USDT | 356 | 48.6% | -6.1% |
| TRX/USDT | 289 | 48.5% | -4.3% |
| BTC/USDT | 213 | 49.3% | -3.8% |
| SOL/USDT | 426 | 49.1% | -2.9% |
| OP/USDT | 378 | 48.8% | -2.1% |
| ARB/USDT | 402 | 48.9% | -1.4% |
| NEAR/USDT | 311 | 49.2% | -0.8% |
| FTM/USDT | 267 | 49.6% | -0.3% |
| APT/USDT | 334 | 49.4% | -0.1% |
| MATIC/USDT | 445 | 49.3% | +0.2% |
| INJ/USDT | 289 | 49.7% | +1.1% |

> Asset selection is critical. The same strategy produces +259.9% on the top 6 and negative results on the bottom 18. Always backtest before adding new pairs.

---

## Requirements

```bash
pip install pandas ta pybit python-dotenv httpx requests
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
pip install pandas ta pybit python-dotenv httpx requests

# Start in background
nohup python3 bot.py > nohup.out 2>&1 &

# Monitor logs
tail -f nohup.out

# Stop
pkill -f bot.py
```

### Update bot from this repo (VPS)
```bash
cd ~/trading_bot && source venv/bin/activate
curl -H "Authorization: token YOUR_TOKEN" \
  -L "https://raw.githubusercontent.com/MarkusSela/bybit-technical-bot/main/bot.py" \
  -o bot.py
nohup python3 bot.py > nohup.out 2>&1 &
```

---

## Backtest

```bash
# Download historical data with Freqtrade
freqtrade download-data --exchange bybit \
  --pairs DOGE/USDT SUI/USDT XRP/USDT XAUT/USDT WLD/USDT ETH/USDT \
  --timeframe 15m 1d --days 180

# Run backtest
python3 backtest_full.py
```

---

## Repository structure

```
bot.py              # Live bot v1.2
backtest_full.py    # 24-asset backtest
README.md           # This file
```

---

## Disclaimer

This bot is for educational purposes. Trading perpetual futures involves significant risk of loss. Past backtest performance does not guarantee future results. Use at your own risk.
