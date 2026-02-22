import logging

from fastapi import APIRouter, HTTPException

from .advisor import run_analysis
from .data import TICKERS, fetch_btc_prices, refresh_all
from .miners import fetch_miner_fundamentals
from .technicals import compute_signals
from . import cache

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/api/analyze")
async def analyze():
    """Fetch latest data and run AI analysis for all tickers."""
    try:
        await fetch_btc_prices()
        await refresh_all()
    except Exception as e:
        raise HTTPException(502, f"Data fetch failed: {e}")

    signals = {ticker: compute_signals(ticker) for ticker in TICKERS}

    # Fetch miner fundamentals — non-fatal if unavailable
    fundamentals = None
    try:
        btc_rows = cache.get_prices("BTC", limit=2)
        btc_price = float(btc_rows[-1]["close"]) if btc_rows else 0
        fundamentals = await fetch_miner_fundamentals(btc_price)
    except Exception as e:
        logger.warning(f"Miner fundamentals fetch failed (non-fatal): {e}")

    try:
        results = await run_analysis(signals, fundamentals)
    except Exception as e:
        raise HTTPException(502, f"AI analysis failed: {e}")

    return {"tickers": results, "fundamentals": fundamentals}


@router.get("/api/signals")
async def get_signals():
    """Return current computed signals from cached data (no API calls)."""
    return {ticker: compute_signals(ticker) for ticker in TICKERS}


@router.get("/api/history/{ticker}")
async def get_history(ticker: str):
    if ticker.upper() not in TICKERS:
        raise HTTPException(404, f"Unknown ticker: {ticker}")
    return cache.get_analysis_history(ticker.upper())
