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

## Pending features

| # | Feature | Description |
|---|---------|-------------|
| 1 | Portfolio tracker | Track holdings and cost basis per ticker |
| 2 | Trade log | Record actual buys/sells with date, price, quantity |
| 3 | Signal accuracy tracker | Compare past recommendations against actual price moves |
| 4 | Signal tuning | Adjustable weights/thresholds for technical signals |
| 5 | Google Sheets export | ✅ Implemented — appends one row per ticker after each analysis run |
