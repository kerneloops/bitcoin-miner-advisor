import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .advisor import run_analysis
from .data import TICKERS, fetch_btc_prices, refresh_all
from .miners import fetch_miner_fundamentals
from .technicals import compute_signals
from . import cache, google_workspace

router = APIRouter()
logger = logging.getLogger(__name__)


class HoldingIn(BaseModel):
    ticker: str
    shares: float
    avg_cost: float


class TradeIn(BaseModel):
    ticker: str
    date: str
    trade_type: str
    price: float
    quantity: float
    notes: str = ""


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


@router.get("/api/export/status")
async def export_status():
    configured = google_workspace.is_configured()
    return {
        "configured": configured,
        "missing": google_workspace._get_missing() if not configured else [],
    }


@router.get("/api/portfolio")
def get_portfolio():
    holdings = cache.get_holdings()
    result = []
    for h in holdings:
        ticker = h["ticker"]
        prices = cache.get_prices(ticker, limit=1)
        current_price = prices[-1]["close"] if prices else None
        history = cache.get_analysis_history(ticker, limit=1)
        latest_rec = history[0]["recommendation"] if history else None

        cost_value   = round(h["avg_cost"] * h["shares"], 2)
        market_value = round(current_price * h["shares"], 2) if current_price else None
        gain_loss_pct = round((current_price / h["avg_cost"] - 1) * 100, 2) if current_price else None

        last_run_price = history[0]["signals"].get("current_price") if history else None
        since_run_pct  = round((current_price / last_run_price - 1) * 100, 2) if (current_price and last_run_price) else None
        since_run_value = round((current_price - last_run_price) * h["shares"], 2) if (current_price and last_run_price) else None

        result.append({
            "ticker": ticker,
            "shares": h["shares"],
            "avg_cost": h["avg_cost"],
            "current_price": current_price,
            "cost_value": cost_value,
            "market_value": market_value,
            "gain_loss_pct": gain_loss_pct,
            "since_run_pct": since_run_pct,
            "since_run_value": since_run_value,
            "recommendation": latest_rec,
        })
    return result


@router.post("/api/portfolio")
def save_holding(body: HoldingIn):
    cache.upsert_holding(body.ticker, body.shares, body.avg_cost)
    return {"ok": True}


@router.delete("/api/portfolio/{ticker}")
def remove_holding(ticker: str):
    cache.delete_ticker_trades(ticker)
    return {"ok": True}


@router.get("/api/trades")
def list_trades():
    return cache.get_trades()


@router.post("/api/trades")
def create_trade(body: TradeIn):
    cache.add_trade(body.ticker, body.date, body.trade_type, body.price, body.quantity, body.notes)
    return {"ok": True}


@router.delete("/api/trades/{trade_id}")
def remove_trade(trade_id: int):
    cache.delete_trade(trade_id)
    return {"ok": True}


@router.post("/api/export")
async def export_to_google(analysis_data: dict):
    if not google_workspace.is_configured():
        raise HTTPException(
            400,
            f"Google not configured. Missing: {', '.join(google_workspace._get_missing())}",
        )

    result = {"sheet": "error", "sheet_url": None}

    try:
        result["sheet_url"] = google_workspace.append_to_sheet(analysis_data)
        result["sheet"] = "ok"
    except Exception as e:
        logger.error(f"Sheets export failed: {e}")
        result["sheet"] = f"error: {e}"

    return result
