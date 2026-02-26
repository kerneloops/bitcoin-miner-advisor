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
| 6 | Monetization plan | Full analysis covering: (a) Security hardening — auth/signup flow, CAPTCHA, rate limiting, HTTPS enforcement, secrets management, input validation, DDoS protection; (b) Scaling — per-user DB isolation or row-level tenancy, Polygon/Anthropic API key pooling, background job queue for analysis, server sizing; (c) Payment integration — Stripe subscription flow, webhook-driven access provisioning, free trial logic; (d) Unit economics at 20 / 40 / 100 subscribers/month — fixed costs (Linode, Polygon paid tier, Anthropic usage, Stripe fees), variable costs per subscriber, suggested price point, gross margin at each tier |
| 7 | LunarCrush social signals | Tier-B signal source (~$29-49/mo). Adds social volume, sentiment, AltRank/Galaxy Score for MARA, RIOT, CIFU etc. from X/Reddit. Fetch on each analysis run, cache in macro_signals table, surface on ticker cards alongside technicals. Best coverage on MARA and RIOT; limited on ETFs (WGMI, BITX). Gives early warning on sentiment spikes before price moves. |
| 8 | Disclaimer pop-up (post sign-up) | Show a one-time modal after initial registration confirming: "LAPIO Terminal does not offer actual, legal or any other binding advice. We are not legally or financially liable for lost capital, cash or any other assets. Invest and trade at your own risk, and remember to do your own research." User must explicitly acknowledge before proceeding. Store acceptance flag in users.db so it only shows once per account. |
| 9 | Login page legal disclaimer | Add a witty, plain-English "what LAPIO Terminal does and doesn't do" section on the login/signup page. Tone: dry humor, not a wall of legalese. Cover: not financial advice, no liability for losses, signals are tools not oracle pronouncements, user is the decision-maker. Tagline territory: "We give you the signals. You make the mistakes." |
| 10 | Native iOS app (time-gated) | Full native iOS app in Swift/SwiftUI. Prerequisite: finish all time-limited features (auth, disclaimers, monetization). Mirrors web feature set — dashboard, ticker cards, trade log, portfolio, macro signals, push notifications for daily analysis. |
| 11 | Signal card height cap + scroll | Limit each signal card's box height so the AI explanation is hidden below the CONFIDENCE row by default; add a scrollbar on the right so users can scroll to read it. |
| 12 | Reduce chat box height by 20% | Shrink the Crisp chat widget / chat box height by ~20% from its current size. |
| 13 | Nest SETTINGS in user profile menu + tour step | Move the SETTINGS link/button inside the user profile dropdown menu instead of being a standalone item. Add a new tour popup step that explains what the Settings panel does (position sizing tiers, Telegram notifications, capital config, etc.). |
| 14 | Broaden Advisor chat scope — market indices + stock quote tool | Two changes to `telegram.py`: (1) Auto-add SPY, QQQ, DIA closing data to the chat context on every build so the Advisor can answer "where did markets close today?" without a tool call. (2) Add a `get_stock_quote` tool (using Polygon.io, already keyed) so the Advisor can look up any stock/ETF live on demand when the user asks about a ticker not in context. |
| 15 | Advisor: richer portfolio analytics in context | Expand `_build_context()` in `telegram.py` to include total portfolio P&L (unrealized), % allocation per ticker, best and worst performer. Data already exists in cache.db — just not surfaced. Enables questions like "how am I doing overall?" and "what's dragging my portfolio?" with zero new dependencies. |
| 16 | Advisor: sector rotation signals in context | Add `vs_sector_1w` and `vs_sector_1m` per ticker to the chat context block. Already computed by `add_relative_strength()` but not passed to `_build_context()`. Lets the Advisor answer "is MARA outperforming the sector or just riding Bitcoin?" |
| 17 | Advisor: earnings calendar + analyst consensus tool | Add a `get_earnings` tool to `telegram.py` using Financial Modeling Prep (FMP) API (free tier: 250 req/day, $14/mo starter). Covers next earnings date, EPS estimate vs actual, analyst consensus rating, and price target. Answers: "when does NVDA report?", "what did MARA earn last quarter?", "what's the analyst target?" Requires new FMP_API_KEY env var. |
| 18 | Advisor: news headlines tool | Add a `get_news` tool using Polygon's `/v2/reference/news` endpoint (already on existing POLYGON_API_KEY — no new cost). Returns recent headlines for any ticker or topic. Lets the Advisor answer "what's moving MARA today?" or "any macro news I should know about?" |
| 19 | Advisor: commodities in macro context | Add gold (GLD or XAUUSD) and crude oil (USO or WTI) to the auto-built macro context in `_build_context()`. Both available via Polygon. Gold signals risk appetite; oil signals miner energy costs. Include 1-day % change alongside VIX/DXY. |
| 20 | Advisor: yield curve shape in macro context | Add the 2s10s spread (10Y minus 2Y Treasury yield) to the macro context. FRED API already opt-in via FRED_API_KEY. One extra line — "Yield curve: +0.15% (normal)" or "Yield curve: -0.30% (inverted — recession signal)". Makes rate environment questions answerable. |
| 21 | Advisor: MVRV ratio on-chain signal | Add Bitcoin MVRV ratio to the macro context. Available free from CoinGecko (`/coins/bitcoin` market data includes it). MVRV < 1 = undervalued / capitulation zone; > 3.5 = historically overheated. Better BTC cycle position signal than price alone. No new API key needed. |
| 22 | Advisor: options flow tool (future — requires Polygon paid tier) | Add a `get_options_flow` tool for unusual options activity on key tickers (MARA, NVDA etc.) using Polygon's options endpoints. Deferred until Polygon plan is upgraded. High signal value for short-term directional bias. |
| 23 | User-configurable watchlist | Let each user choose which tickers appear on their dashboard from the predefined universe lists. Infrastructure mostly exists: `get_active_tickers()` / `add_active_ticker()` are already user-scoped in cache.py, universe groups already defined in data.py, and `refresh_all()` already auto-backfills 365 days for new tickers. Missing pieces: (1) `remove_active_ticker()` in cache.py; (2) GET/POST/DELETE `/api/tickers` endpoint in routes.py; (3) UI in Settings — checkboxes grouped by category mirroring the existing TICKER_UNIVERSE / TECH_TICKER_UNIVERSE structure. Scope note: stocks from predefined lists is low-moderate effort. Optional extensions: (a) free-text custom ticker input with Polygon validation before saving; (b) crypto signal cards (ETH, SOL etc.) — higher effort, requires a new CoinGecko OHLCV data path separate from the stock pipeline. |
| 24 | Rename pages: "Bitcoin Miners" → "Crypto", "Tech Stocks" → "Stocks" | Two-phase rename. Phase 1 (display only, low risk): update tab bar labels, page titles, HTML <title> tags, and any visible copy. Phase 2 (internal slug rename, do alongside item 23): rename universe slugs miners→crypto and tech→stocks throughout routes.py, data.py, advisor.py, telegram.py, app.js, and iOS app. Also migrate existing DB settings keys (active_tickers, macro_bias etc.) stored under the old slugs. Do phase 2 as part of the watchlist build since both touch the same universe structure. Macro panel split stays unchanged — Crypto page keeps DVOL/funding/Fear&Greed/Puell; Stocks page keeps VIX/yields/DXY/HY spread. |
| 25 | **🔜 Polymarket prediction market signals** | **Priority.** Integrate Polymarket's free REST API (`polymarket-apis` PyPI package, 60 req/min, no API key) as a forward-looking signal source for the entire ticker universe — crypto and conventional stocks alike. Phase 1: fetch Fed rate decision probabilities, recession odds, and BTC price bracket contracts. Phase 2: inflation prints, tariff/trade outcomes, geopolitical risk contracts. Cache in `macro_signals` table, surface in both Crypto and Stocks macro panels, pass to Advisor context. Signal value: crowd-sourced implied probabilities — complementary to technicals (backward-looking) and options IV (market-maker-driven). Fed rate probabilities alone are the single highest-signal addition for a multi-asset dashboard. Kalshi can be added later for broader macro coverage. |
