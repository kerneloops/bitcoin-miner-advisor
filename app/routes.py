import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from .advisor import run_analysis
from .data import (
    TICKERS, TICKER_UNIVERSE, TICKER_UNIVERSE_FLAT,
    BENCHMARK_TICKER, fetch_btc_prices, refresh_all, refresh_benchmark,
)
from .macro import fetch_all_macro
from .miners import fetch_miner_fundamentals
from .technicals import add_relative_strength, compute_signals
from . import cache, google_workspace, push, sizing, telegram
from .auth import make_token, verify_token

router = APIRouter()
logger = logging.getLogger(__name__)

_frontend = Path(__file__).parent.parent / "frontend"


@router.get("/login")
def login_page(error: int = 0):
    return FileResponse(_frontend / "login.html")


@router.post("/login")
async def do_login(request: Request):
    form = await request.form()
    password = form.get("password", "")
    app_password = os.getenv("APP_PASSWORD", "")
    if app_password and password == app_password:
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            "session", make_token(),
            httponly=True, samesite="strict",
            max_age=30 * 24 * 3600,  # 30 days
        )
        return response
    return RedirectResponse(url="/login?error=1", status_code=302)


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response


@router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    secret = os.getenv("SESSION_SECRET", "")[:64]
    incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret and incoming != secret:
        raise HTTPException(403, "Forbidden")
    update = await request.json()
    await telegram.handle_update(update)
    return {"ok": True}


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


class CashIn(BaseModel):
    action: str   # "set" | "deposit" | "withdraw"
    amount: float


class ChatSendIn(BaseModel):
    text: str


class PushRegisterIn(BaseModel):
    token: str


def _mobile_auth(request: Request) -> bool:
    """Return True if request has a valid session cookie OR X-App-Password header."""
    cookie = request.cookies.get("session")
    if verify_token(cookie):
        return True
    app_password = os.getenv("APP_PASSWORD", "")
    header_pw = request.headers.get("X-App-Password", "")
    if app_password and header_pw == app_password:
        return True
    return False


@router.get("/api/chat/messages")
async def get_chat_messages(request: Request, limit: int = 100):
    if not _mobile_auth(request):
        raise HTTPException(401, "Unauthorized")
    return cache.get_chat_messages(limit=limit)


@router.post("/api/chat/send")
async def send_chat_message(request: Request, body: ChatSendIn):
    if not _mobile_auth(request):
        raise HTTPException(401, "Unauthorized")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "text must not be empty")
    reply = await telegram.generate_reply(text)
    return {"ok": True, "reply": reply}


@router.post("/api/push/register")
async def register_push_token(request: Request, body: PushRegisterIn):
    if not _mobile_auth(request):
        raise HTTPException(401, "Unauthorized")
    token = body.token.strip()
    if not token:
        raise HTTPException(400, "token must not be empty")
    existing_json = cache.get_setting("push_device_tokens")
    tokens: list = json.loads(existing_json) if existing_json else []
    if token not in tokens:
        tokens.append(token)
        cache.set_setting("push_device_tokens", json.dumps(tokens))
    return {"ok": True}


@router.post("/api/analyze")
async def analyze():
    """Fetch latest data and run AI analysis for all tickers."""
    active_tickers = cache.get_active_tickers(TICKERS)
    try:
        await fetch_btc_prices()
    except Exception as e:
        logger.warning(f"BTC price fetch failed, using cache (non-fatal): {e}")
    try:
        await refresh_all(active_tickers)
    except Exception as e:
        logger.warning(f"Stock price fetch failed, using cache (non-fatal): {e}")

    signals = add_relative_strength({ticker: compute_signals(ticker) for ticker in active_tickers})

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
    active_tickers = cache.get_active_tickers(TICKERS)
    return add_relative_strength({ticker: compute_signals(ticker) for ticker in active_tickers})


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


@router.get("/api/ticker-universe")
def get_ticker_universe():
    """Return the full ticker universe grouped by category and the current active list."""
    active = cache.get_active_tickers(TICKERS)
    return {"universe": TICKER_UNIVERSE, "active": active}


@router.get("/api/benchmark")
async def get_benchmark():
    """Return SPY (S&P 500) performance as a benchmark."""
    from datetime import date, timedelta
    try:
        await refresh_benchmark()
    except Exception as e:
        logger.warning(f"SPY benchmark fetch failed (non-fatal): {e}")

    rows = cache.get_prices(BENCHMARK_TICKER, limit=365)
    if not rows or len(rows) < 2:
        return {"ticker": BENCHMARK_TICKER, "available": False}

    today_price = float(rows[-1]["close"])
    today_date = rows[-1]["date"]
    result: dict = {"ticker": BENCHMARK_TICKER, "current_price": today_price, "available": True}

    for key, days in [("week_return_pct", 7), ("month_return_pct", 30)]:
        cutoff = (date.fromisoformat(today_date) - timedelta(days=days)).isoformat()
        ref = next((r for r in reversed(rows[:-1]) if r["date"] <= cutoff), None)
        if ref:
            result[key] = round((today_price / float(ref["close"]) - 1) * 100, 2)

    ytd_start = f"{date.today().year}-01-01"
    ytd_row = next((r for r in rows if r["date"] >= ytd_start), None)
    if ytd_row:
        result["ytd_return_pct"] = round((today_price / float(ytd_row["close"]) - 1) * 100, 2)

    return result


@router.get("/api/benchmark-chart")
def get_benchmark_chart():
    """Return 30-day normalised % series for SPY and the current portfolio."""
    spy_rows = cache.get_prices(BENCHMARK_TICKER, limit=35)
    if len(spy_rows) < 2:
        return {"available": False}

    spy_rows = spy_rows[-30:]
    spy_base = float(spy_rows[0]["close"])
    spy_series = [
        {"date": r["date"], "pct": round((float(r["close"]) / spy_base - 1) * 100, 2)}
        for r in spy_rows
    ]
    dates = [r["date"] for r in spy_rows]

    holdings = cache.get_all_holdings()
    if not holdings:
        return {"available": True, "spy": spy_series, "portfolio": None}

    # For each date, sum (shares × closing price) across all holdings
    port_by_date: dict[str, float] = {}
    for ticker, shares in holdings.items():
        prices = cache.get_prices(ticker, limit=35)
        if not prices:
            continue
        price_map = {r["date"]: float(r["close"]) for r in prices}
        sorted_dates_avail = sorted(price_map)
        for d in dates:
            price = price_map.get(d)
            if price is None:
                # Use the most recent price on or before this date
                candidates = [k for k in sorted_dates_avail if k <= d]
                if candidates:
                    price = price_map[candidates[-1]]
            if price is not None:
                port_by_date[d] = port_by_date.get(d, 0.0) + shares * price

    if not port_by_date:
        return {"available": True, "spy": spy_series, "portfolio": None}

    first = next((d for d in dates if d in port_by_date), None)
    if not first or port_by_date[first] == 0:
        return {"available": True, "spy": spy_series, "portfolio": None}

    base = port_by_date[first]
    portfolio_series = [
        {"date": d, "pct": round((port_by_date[d] / base - 1) * 100, 2)}
        for d in dates if d in port_by_date
    ]
    return {"available": True, "spy": spy_series, "portfolio": portfolio_series}


@router.get("/api/history/{ticker}")
async def get_history(ticker: str):
    active_tickers = cache.get_active_tickers(TICKERS)
    if ticker.upper() not in active_tickers:
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

        week_return_pct = month_return_pct = None
        try:
            sigs = compute_signals(ticker)
            week_return_pct = sigs.get("week_return_pct")
            month_return_pct = sigs.get("month_return_pct")
        except Exception:
            pass

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
            "week_return_pct": week_return_pct,
            "month_return_pct": month_return_pct,
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
async def create_trade(body: TradeIn):
    if body.trade_type == "SELL":
        holding = cache.get_holdings()
        held = next((h["shares"] for h in holding if h["ticker"] == body.ticker), 0.0)
        if body.quantity > held:
            raise HTTPException(
                400,
                f"Cannot sell {body.quantity} shares of {body.ticker} — only {held} held. "
                "Record a BUY trade first if you have an existing position with no purchase history."
            )
    # Auto-add new tickers from the universe to active tracking and fetch their price history
    active = cache.get_active_tickers(TICKERS)
    if body.ticker not in active and body.ticker in TICKER_UNIVERSE_FLAT:
        cache.add_active_ticker(body.ticker, TICKERS)
        try:
            from .data import refresh_ticker
            await refresh_ticker(body.ticker)
        except Exception as e:
            logger.warning(f"Price fetch for new ticker {body.ticker} (non-fatal): {e}")
    cache.add_trade(body.ticker, body.date, body.trade_type, body.price, body.quantity, body.notes)
    return {"ok": True}


@router.delete("/api/trades/{trade_id}")
def remove_trade(trade_id: int):
    cache.delete_trade(trade_id)
    return {"ok": True}


@router.get("/api/cash")
def get_cash():
    return {"balance": cache.get_cash()}


@router.post("/api/cash")
def update_cash(body: CashIn):
    if body.amount < 0:
        raise HTTPException(400, "Amount must be non-negative.")
    current = cache.get_cash()
    if body.action == "set":
        cache.set_cash(body.amount)
    elif body.action == "deposit":
        cache.set_cash(current + body.amount)
    elif body.action == "withdraw":
        cache.set_cash(current - body.amount)
    else:
        raise HTTPException(400, f"Unknown action '{body.action}'. Use set/deposit/withdraw.")
    return {"balance": cache.get_cash()}


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
