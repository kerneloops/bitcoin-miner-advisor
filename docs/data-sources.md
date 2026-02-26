# LAPIO Signals Terminal — Data Sources

How LAPIO generates its daily signals and powers the Advisor chat.

## Market Data

| Source | Data | Coverage |
|--------|------|----------|
| **Polygon.io** | Daily OHLCV prices (open, high, low, close, volume) | US stocks & ETFs |
| **CoinGecko** | Crypto prices, market cap, 24h/7d/30d performance | BTC + altcoins |

## Technical Signals (computed internally)

All technical indicators are calculated from cached price history — no third-party TA service.

- **RSI(14)** — 14-day Relative Strength Index
- **SMA 20 / SMA 50** — short- and medium-term moving averages
- **1-week & 1-month returns** — momentum
- **BTC rolling correlation** — how closely a stock tracks Bitcoin

## Crypto On-Chain & Derivatives

| Source | Data |
|--------|------|
| **Mempool.space** | Network hashrate, difficulty adjustment, hashprice (miner revenue per TH/s), Puell Multiple |
| **Deribit** | BTC 30-day implied volatility (DVOL index) |
| **Bybit / OKX** | BTC perpetual funding rate (market positioning signal) |
| **Alternative.me** | Crypto Fear & Greed Index (0–100 sentiment gauge) |

## US Macro Indicators

| Source | Data |
|--------|------|
| **FRED (Federal Reserve of St. Louis)** | VIX (equity volatility), 2-Year Treasury yield, US Dollar Index (DXY), High-Yield credit spread |

## AI Analysis Engine

| Source | Role |
|--------|------|
| **Anthropic Claude** | Synthesizes all of the above into a daily BUY / SELL / HOLD recommendation per ticker, with a confidence score, written reasoning, and key risk factor. Also powers the Advisor chat for follow-up questions. |

## Coming Soon

| Source | Data |
|--------|------|
| **Polymarket** | Prediction market probabilities — Fed rate decisions, recession odds, BTC price targets. Crowd-sourced forward-looking signals that complement backward-looking technicals. |

---

*LAPIO shows you where the data comes from. The signal synthesis — how these inputs are weighted, combined, and interpreted — is where the product lives.*
