# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Daily buy/sell/hold decision tool for Bitcoin miner ETFs and stocks (WGMI, MARA, RIOT, BITX). Fetches price data, computes technical signals, and uses Claude AI to generate structured recommendations.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in POLYGON_API_KEY and ANTHROPIC_API_KEY
```

## Running

```bash
uvicorn main:app --reload
# Open http://localhost:8000
```

## Architecture

**Backend** — FastAPI app (`main.py`) with three layers:

| Module | Role |
|--------|------|
| `app/cache.py` | SQLite persistence (`data/cache.db`). Stores OHLCV prices and analysis history. |
| `app/data.py` | HTTP clients for Polygon.io (stock OHLCV) and CoinGecko (BTC). Incremental fetch — only pulls candles newer than the latest cached date. |
| `app/technicals.py` | Computes RSI(14), SMA20, SMA50, 1W/1M returns, BTC rolling correlation from cached data. |
| `app/advisor.py` | Sends signals to `claude-haiku-4-5-20251001` and parses a structured JSON recommendation (BUY/SELL/HOLD + confidence + reasoning + key risk). |
| `app/routes.py` | Five endpoints: `POST /api/analyze` (full pipeline), `GET /api/signals` (signals only, no API calls), `GET /api/history/{ticker}`, `GET /api/export/status`, `POST /api/export` (append to Google Sheet) |
| `app/google_workspace.py` | Google Sheets export via service account. Appends one row per ticker; auto-creates header on first run. Requires `GOOGLE_SERVICE_ACCOUNT_JSON` and `GOOGLE_SHEET_ID` env vars. |

**Frontend** — Vanilla JS/CSS served as static files from `frontend/`. No build step.

## Key design decisions

- **Caching strategy**: First run fetches 365 days of history; subsequent runs fetch only new candles. This avoids Polygon rate limits entirely.
- **BTC data**: Fetched from CoinGecko (free, no key required) and stored in the same `prices` table under ticker `"BTC"`.
- **AI model**: Uses Haiku (fast, cheap) not Sonnet — each daily run is ~4 AI calls.
- **Tickers**: Defined in `app/data.py::TICKERS`. Edit there to add/remove instruments.

## Roadmap

### Now — ship these first, in order

| P | Feature | Description |
|---|---------|-------------|
| 1 | Signal accuracy tracker | Compare past recommendations against actual price moves (7d, 14d, 30d). Prove the product works before adding anything else. Data already in `analysis_history` — needs a backtest loop and a dashboard section showing hit rate. |
| 2 | User-configurable watchlist | Let each user pick tickers from predefined universe lists. Broadens the product from 4 miner ETFs to the full stock + crypto universe. Infrastructure half-built: `get_active_tickers()` / `add_active_ticker()` exist in cache.py, universe groups in data.py, `refresh_all()` auto-backfills. Missing: `remove_active_ticker()`, GET/POST/DELETE `/api/tickers`, Settings UI with grouped checkboxes. |
| 3 | Monetization | Stripe subscription flow, webhook-driven access provisioning, free trial logic. Unit economics at 20/40/100 subs. Security hardening (CAPTCHA, rate limiting, secrets management) as prereqs. |
| 4 | Disclaimers (post-signup + login page) | One-time modal after registration ("not financial advice, trade at your own risk") with stored acceptance flag. Plus witty plain-English disclaimer on login page. Legal cover before taking money. |
| 5 | Portfolio tracker + trade log | Track holdings, cost basis, and actual buys/sells per ticker. Enables P&L tracking and makes the product sticky — users won't leave once their data lives here. |

### Later — revisit after the above ship

| Feature | Notes |
|---------|-------|
| Polymarket prediction market signals | Free API, high signal value (Fed rates, recession odds). Build once monetization validates demand. |
| Advisor: news headlines tool | Uses existing Polygon key, no new cost. Most useful of the Advisor tools. |
| Advisor: market indices + stock quote tool | SPY/QQQ/DIA context + live quote lookup. Natural companion to watchlist expansion. |
| Advisor: portfolio analytics in context | Surface P&L, allocation, best/worst performer. Depends on portfolio tracker shipping first. |
| Advisor: sector rotation signals | Already computed, just not surfaced. Low effort. |
| Advisor: commodities in macro context | Gold + oil via Polygon. Low effort. |
| Advisor: yield curve in macro context | 2s10s spread via FRED. One extra line. |
| Advisor: MVRV ratio on-chain signal | Free via CoinGecko. Low effort. |
| Advisor: earnings calendar + analyst consensus | Requires FMP API ($14/mo). Add when paying users justify the cost. |
| Advisor: options flow tool | Blocked — requires Polygon paid tier upgrade. |
| LunarCrush social signals | $29-49/mo. Add when paying users justify the cost. |
| Signal tuning | Adjustable weights/thresholds. Nice-to-have once accuracy tracker proves baseline. |
| Rename universe slugs (phase 2 of #24) | Internal slug rename (miners→crypto, tech→stocks). Do alongside watchlist if touching same code. |
| Nest SETTINGS in profile menu + tour step | UI polish. |
| Native iOS app | Ship after product-market fit is proven on web. |

### Done

| Feature | Status |
|---------|--------|
| Google Sheets export | ✅ Implemented |
| Signal card height cap + scroll | ✅ Implemented |
| Reduce chat box height | ✅ Implemented |
| Rename page tabs (display only) | ✅ Implemented |
