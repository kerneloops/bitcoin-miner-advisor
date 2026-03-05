"""Fetch market-wide macro and on-chain signals."""

import asyncio
import json
import logging
import os
import time
from datetime import date

import httpx

from . import cache

logger = logging.getLogger(__name__)


async def _fetch_dvol() -> float | None:
    """BTC 30-day implied volatility (DVOL) from Deribit — free public API."""
    try:
        end_ts = int(time.time() * 1000)
        start_ts = end_ts - 7 * 24 * 3600 * 1000  # 7 days back
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://www.deribit.com/api/v2/public/get_volatility_index_data",
                params={
                    "currency": "BTC",
                    "resolution": "86400",
                    "start_timestamp": start_ts,
                    "end_timestamp": end_ts,
                },
            )
            r.raise_for_status()
            rows = r.json()["result"]["data"]
            if rows:
                return round(float(rows[-1][4]), 1)  # latest daily close
    except Exception as e:
        logger.warning(f"DVOL fetch failed: {e}")
    return None


async def _fetch_funding_rate() -> float | None:
    """BTC perpetual funding rate — tries Bybit then OKX (both free, no auth)."""
    # Try Bybit first
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.bybit.com/v5/market/funding/history",
                params={"category": "linear", "symbol": "BTCUSDT", "limit": "1"},
            )
            r.raise_for_status()
            rows = r.json().get("result", {}).get("list", [])
            if rows:
                return round(float(rows[0]["fundingRate"]) * 100, 4)
    except Exception as e:
        logger.warning(f"Bybit funding rate failed, trying OKX: {e}")

    # Fallback: OKX
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://www.okx.com/api/v5/public/funding-rate",
                params={"instId": "BTC-USDT-SWAP"},
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            if data:
                return round(float(data[0]["fundingRate"]) * 100, 4)
    except Exception as e:
        logger.warning(f"OKX funding rate failed: {e}")

    return None


async def _fetch_fear_greed() -> dict | None:
    """Crypto Fear & Greed Index from Alternative.me — free, no key."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.alternative.me/fng/?limit=1")
            r.raise_for_status()
            entry = r.json()["data"][0]
            return {"value": int(entry["value"]), "label": entry["value_classification"]}
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
    return None


async def _fetch_puell() -> dict | None:
    """Hash rate + Puell Multiple via mempool.space (free, no key)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Hash rate history (1 year of daily blocks)
            r = await client.get("https://mempool.space/api/v1/mining/hashrate/1y")
            r.raise_for_status()
            data = r.json()

            hashrates = data.get("hashrates", [])
            if not hashrates:
                return None

            # Latest hash rate in EH/s
            latest_hs = float(hashrates[-1]["avgHashrate"])
            hash_rate_eh = round(latest_hs / 1e18, 1)

            # Puell Multiple: daily BTC issuance revenue / 365d MA
            # Use difficulty and block subsidy to estimate miner revenue
            # Simpler proxy: use difficulty data from mempool
            difficulty_data = data.get("difficulty", [])
            if not difficulty_data:
                return {"hash_rate_eh": hash_rate_eh, "puell_multiple": None}

            # Get BTC price for USD revenue calculation
            btc_rows = cache.get_prices("BTC", limit=400)
            if len(btc_rows) < 30:
                return {"hash_rate_eh": hash_rate_eh, "puell_multiple": None}

            # Daily BTC issuance ≈ 144 blocks * current subsidy (3.125 BTC post-halving)
            # Revenue in USD = issuance * BTC price
            # Puell = today's revenue / 365d MA
            btc_prices = {row["date"]: float(row["close"]) for row in btc_rows}
            sorted_dates = sorted(btc_prices.keys())

            # Daily issuance (BTC) — approximate, assumes post-April 2024 halving
            subsidy = 3.125
            daily_issuance = 144 * subsidy

            daily_revenues = [btc_prices[d] * daily_issuance for d in sorted_dates]
            if not daily_revenues:
                return {"hash_rate_eh": hash_rate_eh, "puell_multiple": None}

            window = daily_revenues[-365:] if len(daily_revenues) >= 365 else daily_revenues
            avg_rev = sum(window) / len(window)
            current_rev = daily_revenues[-1]
            puell = round(current_rev / avg_rev, 3) if avg_rev > 0 else None

            return {"hash_rate_eh": hash_rate_eh, "puell_multiple": puell}
    except Exception as e:
        logger.warning(f"Puell/hashrate fetch failed: {e}")
    return None


async def _fetch_fred(series_id: str, api_key: str) -> float | None:
    """Fetch latest observation for a FRED series — free with API key."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": "5",
                },
            )
            r.raise_for_status()
            for obs in r.json()["observations"]:
                if obs["value"] != ".":
                    return round(float(obs["value"]), 4)
    except Exception as e:
        logger.warning(f"FRED {series_id} fetch failed: {e}")
    return None


def _pm_prices(mkt: dict) -> list[float]:
    """Parse outcomePrices (JSON string or list) into list of floats."""
    raw = mkt.get("outcomePrices", [])
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [float(p) for p in raw] if raw else []


async def _fetch_polymarket() -> dict:
    """Polymarket prediction market signals via Gamma API (free, no auth)."""
    result = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            fed_resp, econ_resp = await asyncio.gather(
                client.get("https://gamma-api.polymarket.com/events", params={
                    "tag_slug": "fed-rates", "active": "true", "closed": "false", "limit": "10",
                }),
                client.get("https://gamma-api.polymarket.com/events", params={
                    "tag_slug": "economy", "active": "true", "closed": "false", "limit": "10",
                }),
                return_exceptions=True,
            )

            # --- FOMC hold odds + rate cuts 2026 ---
            if not isinstance(fed_resp, Exception):
                fed_resp.raise_for_status()
                for event in fed_resp.json():
                    slug = event.get("slug", "")

                    # Next FOMC decision — pick first matching event
                    if slug.startswith("fed-decision-in-") and "pm_fed_hold_pct" not in result:
                        for mkt in event.get("markets", []):
                            if (mkt.get("groupItemTitle") or "").lower() == "no change":
                                prices = _pm_prices(mkt)
                                if prices:
                                    result["pm_fed_hold_pct"] = round(prices[0] * 100, 1)
                                    title = event.get("title", "")
                                    meeting = title.replace("Fed decision in ", "").rstrip("?").strip()
                                    result["pm_fed_meeting"] = meeting
                                break

                    # 2026 rate cuts — highest-probability outcome
                    if "rate-cuts" in slug and "2026" in slug:
                        best_label, best_pct = None, 0.0
                        for mkt in event.get("markets", []):
                            prices = _pm_prices(mkt)
                            if prices:
                                yes_pct = round(prices[0] * 100, 1)
                                if yes_pct > best_pct:
                                    best_pct = yes_pct
                                    best_label = mkt.get("groupItemTitle", "")
                        if best_label:
                            result["pm_fed_cuts_2026"] = best_label
                            result["pm_fed_cuts_2026_pct"] = best_pct

            # --- US recession ---
            if not isinstance(econ_resp, Exception):
                econ_resp.raise_for_status()
                for event in econ_resp.json():
                    if "recession" in event.get("slug", ""):
                        markets = event.get("markets", [])
                        if markets:
                            prices = _pm_prices(markets[0])
                            if prices:
                                result["pm_recession_pct"] = round(prices[0] * 100, 1)
                        break

    except Exception as e:
        logger.warning(f"Polymarket fetch failed: {e}")
    return result


async def fetch_all_macro() -> dict:
    """Fetch all Tier-A macro signals concurrently and cache results."""
    fred_key = os.getenv("FRED_API_KEY", "").strip()
    fred_map = {
        "VIXCLS":       "vix",
        "DGS2":         "us_2y_yield",
        "DTWEXBGS":     "dxy",
        "BAMLH0A0HYM2": "hy_spread",
    }

    base_tasks = [_fetch_dvol(), _fetch_funding_rate(), _fetch_fear_greed(), _fetch_puell(), _fetch_polymarket()]
    fred_tasks = [_fetch_fred(sid, fred_key) for sid in fred_map] if fred_key else []

    results = await asyncio.gather(*base_tasks, *fred_tasks, return_exceptions=True)
    dvol, funding, fear_greed, puell_data, polymarket_data = results[:5]
    fred_results = list(results[5:])

    macro: dict = {}

    if isinstance(dvol, float):
        macro["btc_dvol"] = dvol
    if isinstance(funding, float):
        macro["btc_funding_rate_pct"] = funding
    if isinstance(fear_greed, dict):
        macro["fear_greed_value"] = fear_greed["value"]
        macro["fear_greed_label"] = fear_greed["label"]
    if isinstance(puell_data, dict):
        macro.update(puell_data)
    if isinstance(polymarket_data, dict):
        macro.update(polymarket_data)
    for key, val in zip(fred_map.values(), fred_results):
        if isinstance(val, float):
            macro[key] = val

    if macro:
        cache.upsert_macro(date.today().isoformat(), macro)

    return macro
