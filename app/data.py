import asyncio
import logging
import os
from datetime import date, timedelta

import httpx

from . import cache

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

DEFAULT_TICKERS = [
    "WGMI", "MARA", "RIOT", "BITX", "RIOX", "CIFU", "BMNU", "MSTX",
    "NVDA", "AMD", "MSFT", "GOOGL", "META", "TSLA", "AMZN", "AAPL", "PLTR", "ARM", "ANET", "VRT", "NBIS",
]

# Full ticker universe grouped by category — merged crypto + tech
TICKER_UNIVERSE: dict[str, list[str]] = {
    "Bitcoin Miners": ["WGMI", "MARA", "RIOT", "RIOX", "CIFU", "BMNU", "CLSK", "HUT", "IREN", "CORZ", "BTBT"],
    "Bitcoin ETFs": ["BITX", "MSTX", "IBIT", "FBTC"],
    "AI & Semiconductors": ["NVDA", "AMD", "INTC", "QCOM", "AVGO", "ARM", "MRVL", "AMAT", "LRCX"],
    "AI Infrastructure": ["VRT", "ANET", "NBIS", "SMCI", "DELL"],
    "Mega Cap Tech": ["AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "TSLA"],
    "AI Pure Plays": ["PLTR", "AI", "BBAI", "PATH", "SOUN"],
    "Enterprise & Cloud": ["CRM", "ORCL", "NOW", "SNOW", "NET", "DDOG"],
}
TICKER_UNIVERSE_FLAT: list[str] = [t for tickers in TICKER_UNIVERSE.values() for t in tickers]

# Category groups for classify_ticker()
_CRYPTO_GROUPS = {"Bitcoin Miners", "Bitcoin ETFs"}
_TECH_GROUPS = {"AI & Semiconductors", "AI Infrastructure", "Mega Cap Tech", "AI Pure Plays", "Enterprise & Cloud"}

# Pre-built lookup: ticker → category name
_TICKER_TO_CATEGORY: dict[str, str] = {}
for _cat, _tlist in TICKER_UNIVERSE.items():
    for _t in _tlist:
        _TICKER_TO_CATEGORY[_t] = _cat


def classify_ticker(ticker: str) -> str:
    """Return 'crypto', 'tech', or 'generic' based on universe group membership."""
    cat = _TICKER_TO_CATEGORY.get(ticker)
    if cat in _CRYPTO_GROUPS:
        return "crypto"
    if cat in _TECH_GROUPS:
        return "tech"
    return "generic"


async def search_tickers(query: str, limit: int = 8) -> list[dict]:
    """Search Polygon for tickers matching query. Returns [{ticker, name, market, type}]."""
    api_key = os.environ.get("POLYGON_API_KEY", "")
    if not api_key or not query:
        return []
    url = f"{POLYGON_BASE}/v3/reference/tickers"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params={
            "search": query,
            "active": "true",
            "market": "stocks",
            "limit": limit,
            "apiKey": api_key,
        })
        if resp.status_code != 200:
            return []
        data = resp.json()
    return [
        {"ticker": r["ticker"], "name": r.get("name", ""), "market": r.get("market", ""), "type": r.get("type", "")}
        for r in data.get("results", [])
    ]


BENCHMARK_TICKER = "SPY"


async def fetch_polygon(ticker: str, from_date: str, to_date: str) -> list[dict]:
    api_key = os.environ["POLYGON_API_KEY"]
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params={"apiKey": api_key, "limit": 500, "sort": "asc"})
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 15))
            logger.warning(f"Polygon rate limit hit for {ticker}, retrying in {wait}s")
            await asyncio.sleep(wait)
            resp = await client.get(url, params={"apiKey": api_key, "limit": 500, "sort": "asc"})
        resp.raise_for_status()
        data = resp.json()

    rows = []
    for r in data.get("results", []):
        rows.append({
            "date": date.fromtimestamp(r["t"] / 1000).isoformat(),
            "open": r["o"],
            "high": r["h"],
            "low": r["l"],
            "close": r["c"],
            "volume": int(r["v"]),
        })
    return rows


async def refresh_ticker(ticker: str):
    today = date.today().isoformat()
    latest = cache.get_latest_date(ticker)

    if latest is None:
        from_date = (date.today() - timedelta(days=365)).isoformat()
    else:
        from_date = (date.fromisoformat(latest) + timedelta(days=1)).isoformat()

    if from_date > today:
        return  # Already up to date

    try:
        rows = await fetch_polygon(ticker, from_date, today)
        if rows:
            cache.upsert_prices(ticker, rows)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 404) and latest is not None:
            # Non-trading day or free-tier restriction — use cached data
            return
        raise


async def refresh_all(tickers: list[str] | None = None):
    for ticker in (tickers or DEFAULT_TICKERS):
        await refresh_ticker(ticker)


async def refresh_benchmark():
    await refresh_ticker(BENCHMARK_TICKER)


async def fetch_btc_prices(days: int = 90):
    url = f"{COINGECKO_BASE}/coins/bitcoin/market_chart"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params={"vs_currency": "usd", "days": days, "interval": "daily"})
        resp.raise_for_status()
        data = resp.json()

    rows = []
    for ts, price in data["prices"]:
        rows.append({
            "date": date.fromtimestamp(ts / 1000).isoformat(),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 0,
        })

    if rows:
        cache.upsert_prices("BTC", rows)

    return rows
