"""
Bitcoin network fundamentals: hashprice, difficulty, hashrate.
Data sourced from mempool.space (free, no API key).
Hashprice is derived from block reward + BTC price + network hashrate.
"""

from datetime import datetime, timezone

import httpx

MEMPOOL_BASE = "https://mempool.space/api/v1"
BLOCK_REWARD_BTC = 3.125  # post-April 2024 halving, valid until ~2028
BLOCKS_PER_DAY = 144


def _hashprice_usd(btc_price: float, hashrate_h_per_s: float) -> float:
    """
    Hashprice = daily miner revenue per TH/s of hashrate.
    Excludes transaction fees (typically adds 10-25% on top).
    Formula: (block_reward × blocks_per_day × btc_price) / hashrate_TH_per_s
    """
    hashrate_th = hashrate_h_per_s / 1e12
    daily_revenue = BLOCK_REWARD_BTC * BLOCKS_PER_DAY * btc_price
    return daily_revenue / hashrate_th


async def fetch_miner_fundamentals(btc_price: float) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        diff_resp = await client.get(f"{MEMPOOL_BASE}/difficulty-adjustment")
        diff_resp.raise_for_status()
        diff = diff_resp.json()

        hr_resp = await client.get(f"{MEMPOOL_BASE}/mining/hashrate/1w")
        hr_resp.raise_for_status()
        hr = hr_resp.json()

    current_hashrate_h = hr.get("currentHashrate", 0)
    hashrate_eh = current_hashrate_h / 1e18  # H/s → EH/s

    hashprice = _hashprice_usd(btc_price, current_hashrate_h) if current_hashrate_h else None

    # Block time in minutes (adjustedTimeAvg is in ms)
    block_time_min = round(diff.get("adjustedTimeAvg", 600000) / 60000, 1)

    # Days until difficulty adjustment
    remaining_ms = diff.get("remainingTime", 0)
    days_until_retarget = round(remaining_ms / 86400000, 1)

    # Estimated retarget date
    retarget_ts = diff.get("estimatedRetargetDate", 0)
    retarget_date = datetime.fromtimestamp(retarget_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    return {
        "hashprice_usd_per_th_day": round(hashprice, 4) if hashprice else None,
        "hashprice_usd_per_ph_day": round(hashprice * 1000, 2) if hashprice else None,
        "network_hashrate_eh": round(hashrate_eh, 1),
        "difficulty_change_pct": round(diff.get("difficultyChange", 0), 2),
        "difficulty_progress_pct": round(diff.get("progressPercent", 0), 1),
        "previous_retarget_pct": round(diff.get("previousRetarget", 0), 2),
        "remaining_blocks": diff.get("remainingBlocks"),
        "days_until_retarget": days_until_retarget,
        "estimated_retarget_date": retarget_date,
        "block_time_min": block_time_min,
        "note": "Hashprice excludes tx fees; actual hashprice ~10-25% higher",
    }
