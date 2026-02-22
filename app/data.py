import asyncio
import logging
import os
from datetime import date, timedelta

import httpx

from . import cache

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

TICKERS = ["WGMI", "MARA", "RIOT", "BITX"]


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


async def refresh_all():
    for ticker in TICKERS:
        await refresh_ticker(ticker)


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
