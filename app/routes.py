import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .advisor import run_analysis
from .data import TICKERS, fetch_btc_prices, refresh_all
from .macro import fetch_all_macro
from .miners import fetch_miner_fundamentals
from .technicals import add_relative_strength, compute_signals
from . import cache, google_workspace, sizing, telegram

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


class SettingsIn(BaseModel):
    risk_tier: str | None = None
    total_capital: float | None = None


@router.post("/api/analyze")
async def analyze():
    """Fetch latest data and run AI analysis for all tickers."""
    try:
        await fetch_btc_prices()
        await refresh_all()
    except Exception as e:
        raise HTTPException(502, f"Data fetch failed: {e}")

    signals = add_relative_strength({ticker: compute_signals(ticker) for ticker in TICKERS})

    # Fetch miner fundamentals and macro signals — non-fatal if unavailable
    fundamentals = None
    try:
        btc_rows = cache.get_prices("BTC", limit=2)
        btc_price = float(btc_rows[-1]["close"]) if btc_rows else 0
        fundamentals = await fetch_miner_fundamentals(btc_price)
    except Exception as e:
        logger.warning(f"Miner fundamentals fetch failed (non-fatal): {e}")

    macro = None
    try:
        macro = await fetch_all_macro()
    except Exception as e:
        logger.warning(f"Macro fetch failed (non-fatal): {e}")
    if not macro:
        macro = cache.get_latest_macro() or None

    try:
        results = await run_analysis(signals, fundamentals, macro)
    except Exception as e:
        raise HTTPException(502, f"AI analysis failed: {e}")

    # Attach position guidance to each ticker result
    tier_name = cache.get_setting("risk_tier", "neutral")
    cap_str = cache.get_setting("total_capital")
    total_capital = float(cap_str) if cap_str else None
    holdings = cache.get_all_holdings()

    for ticker, d in results.items():
        try:
            guidance = sizing.compute_guidance(
                ticker=ticker,
                rec=d.get("recommendation"),
                confidence=d.get("confidence"),
                price=d.get("current_price"),
                shares_held=holdings.get(ticker, 0),
                tier_name=tier_name,
                total_capital=total_capital,
            )
            d["position_guidance"] = guidance
        except Exception as e:
            logger.warning(f"Sizing guidance failed for {ticker} (non-fatal): {e}")
            d["position_guidance"] = None

    # Send Telegram notifications — non-fatal
    try:
        await telegram.notify_signals(results)
    except Exception as e:
        logger.warning(f"Telegram notification failed (non-fatal): {e}")

    macro_bias = cache.get_setting("macro_bias")
    return {"tickers": results, "fundamentals": fundamentals, "macro": macro, "macro_bias": macro_bias}


@router.get("/api/signals")
async def get_signals():
    """Return current computed signals from cached data (no API calls)."""
    return add_relative_strength({ticker: compute_signals(ticker) for ticker in TICKERS})


@router.get("/api/settings")
def get_settings():
    return {
        "risk_tier": cache.get_setting("risk_tier", "neutral"),
        "total_capital": float(cache.get_setting("total_capital", "0") or "0"),
        "telegram_configured": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
    }


@router.post("/api/settings")
def save_settings(body: SettingsIn):
    if body.risk_tier is not None:
        if body.risk_tier not in sizing.TIERS:
            raise HTTPException(400, f"Invalid risk_tier. Must be one of: {list(sizing.TIERS)}")
        cache.set_setting("risk_tier", body.risk_tier)
    if body.total_capital is not None:
        cache.set_setting("total_capital", str(body.total_capital))
    return {"ok": True}


@router.post("/api/notifications/test")
async def test_notification():
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not configured in .env")
    ok, err = await telegram.send_message(
        "✅ <b>LAPIO TEST</b>\n\nTelegram notifications are working correctly.\n\nlapio.dev"
    )
    if not ok:
        raise HTTPException(502, f"Telegram error: {err}")
    return {"ok": True}


@router.get("/api/macro")
def get_macro():
    """Return latest cached macro signals plus macro bias."""
    data = cache.get_latest_macro()
    bias = cache.get_setting("macro_bias")
    if bias:
        data["macro_bias"] = bias
    return data


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
    if body.trade_type == "SELL":
        holding = cache.get_holdings()
        held = next((h["shares"] for h in holding if h["ticker"] == body.ticker), 0.0)
        if body.quantity > held:
            raise HTTPException(
                400,
                f"Cannot sell {body.quantity} shares of {body.ticker} — only {held} held. "
                "Record a BUY trade first if you have an existing position with no purchase history."
            )
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
