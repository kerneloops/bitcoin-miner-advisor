import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .advisor import run_analysis
from .data import (
    TICKERS, TICKER_UNIVERSE, TICKER_UNIVERSE_FLAT,
    TECH_TICKERS, TECH_TICKER_UNIVERSE_FLAT,
    BENCHMARK_TICKER, fetch_btc_prices, refresh_all, refresh_benchmark,
    get_tickers_for_universe,
)
from .macro import fetch_all_macro
from .miners import fetch_miner_fundamentals
from .technicals import add_relative_strength, compute_signals
from . import cache, google_workspace, push, sizing, telegram
from . import users as user_store

router = APIRouter()
logger = logging.getLogger(__name__)

_frontend = Path(__file__).parent.parent / "frontend"


# ── Auth pages ───────────────────────────────────────────────────────────────

@router.get("/login")
def login_page():
    return FileResponse(_frontend / "login.html")


@router.get("/tech")
def tech_page():
    return FileResponse(_frontend / "tech.html")


@router.post("/login")
async def do_login(request: Request):
    """Form-based login (sets session cookie, redirects to /)."""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    user = user_store.verify_password(username, password)
    if user:
        ua = request.headers.get("User-Agent", "")
        token = user_store.create_session(user["id"], ua)
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            "session", token,
            httponly=True, samesite="strict",
            max_age=30 * 24 * 3600,
        )
        return response
    return RedirectResponse(url="/login?error=1", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    user_store.delete_session(token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response


# ── JSON auth API (used by iOS and web JS) ───────────────────────────────────

class AuthLoginIn(BaseModel):
    username: str
    password: str


class AuthRegisterIn(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
async def api_login(body: AuthLoginIn, request: Request):
    user = user_store.verify_password(body.username, body.password)
    if not user:
        raise HTTPException(401, "invalid_credentials")
    ua = request.headers.get("User-Agent", "")
    token = user_store.create_session(user["id"], ua)
    response = JSONResponse(
        {"token": token, "username": user["username"], "user_id": user["id"]}
    )
    response.set_cookie(
        "session", token,
        httponly=True, samesite="strict",
        max_age=30 * 24 * 3600,
    )
    return response


@router.post("/api/auth/register")
async def api_register(body: AuthRegisterIn, request: Request):
    max_users = int(os.getenv("MAX_USERS", "5"))
    was_first = user_store.is_first_user()
    try:
        result = user_store.create_user(body.username, body.password, max_users)
    except user_store.RegistrationError as e:
        if e.code == "beta_full":
            raise HTTPException(403, "beta_full")
        elif e.code == "username_taken":
            raise HTTPException(409, "username_taken")
        elif e.code == "password_too_short":
            raise HTTPException(422, "password_too_short")
        raise
    if was_first:
        user_store.claim_legacy_data(result["id"])
    ua = request.headers.get("User-Agent", "")
    token = user_store.create_session(result["id"], ua)
    response = JSONResponse(
        {"token": token, "username": result["username"], "user_id": result["id"]}
    )
    response.set_cookie(
        "session", token,
        httponly=True, samesite="strict",
        max_age=30 * 24 * 3600,
    )
    return response


@router.get("/api/auth/me")
async def api_me(request: Request):
    token = request.cookies.get("session") or request.headers.get("X-Session-Token")
    user = user_store.get_session(token)
    if not user:
        raise HTTPException(401, "Unauthorized")
    return {"username": user["username"], "user_id": user["user_id"]}


@router.post("/api/support")
async def submit_support(request: Request):
    body = await request.json()
    name    = str(body.get("name", "")).strip()
    email   = str(body.get("email", "")).strip()
    message = str(body.get("message", "")).strip()
    if not name or not email or not message:
        raise HTTPException(400, "name, email, and message are required")
    if len(message) > 2000:
        raise HTTPException(400, "message too long (max 2000 chars)")
    cache.save_support_message(name, email, message)

    # Send via Resend if configured
    resend_key = os.getenv("RESEND_API_KEY", "")
    support_to = os.getenv("SUPPORT_TO_EMAIL", "support@lapio.dev")
    if resend_key:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}"},
                    json={
                        "from": "Lapio Support <support@lapio.dev>",
                        "to": [support_to],
                        "reply_to": email,
                        "subject": f"[Lapio Support] Message from {name}",
                        "text": f"Name: {name}\nEmail: {email}\n\n{message}",
                    },
                    timeout=10,
                )
        except Exception as e:
            logger.warning(f"Resend email failed (non-fatal): {e}")

    # Also forward to Telegram
    try:
        await telegram.send_message(
            f"📬 <b>Support message</b>\n"
            f"From: {name} &lt;{email}&gt;\n\n"
            f"{message}"
        )
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/auth/logout")
async def api_logout(request: Request):
    token = request.cookies.get("session") or request.headers.get("X-Session-Token")
    user_store.delete_session(token)
    return {"ok": True}


# ── Telegram webhook ─────────────────────────────────────────────────────────

@router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    secret = os.getenv("SESSION_SECRET", "")[:64]
    incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret and incoming != secret:
        raise HTTPException(403, "Forbidden")
    update = await request.json()
    await telegram.handle_update(update)
    return {"ok": True}


# ── Pydantic models ───────────────────────────────────────────────────────────

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


class PrivateCompanyIn(BaseModel):
    name: str
    sector: str = ""
    stage: str = "Pre-IPO"
    last_valuation_b: float | None = None
    last_round_type: str = ""
    last_round_amount_m: float | None = None
    last_round_date: str = ""
    notes: str = ""
    forge_url: str | None = None


class SecondaryPriceIn(BaseModel):
    price: float | None = None


# ── BTC ticker ───────────────────────────────────────────────────────────────

_btc_ticker_cache: dict = {}
_btc_ticker_ts: float = 0.0
_BTC_TICKER_TTL = 120  # seconds


@router.get("/api/btc-ticker")
async def btc_ticker():
    global _btc_ticker_cache, _btc_ticker_ts
    import httpx
    if _btc_ticker_cache and (time.time() - _btc_ticker_ts) < _BTC_TICKER_TTL:
        return _btc_ticker_cache
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/coins/bitcoin",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "false",
                    "developer_data": "false",
                },
            )
            resp.raise_for_status()
            md = resp.json()["market_data"]
        result = {
            "usd": {
                "price": md["current_price"]["usd"],
                "change_24h": md["price_change_percentage_24h"],
                "change_7d": md["price_change_percentage_7d"],
                "change_30d": md["price_change_percentage_30d"],
            },
            "eur": {
                "price": md["current_price"]["eur"],
                "change_24h": md["price_change_percentage_24h_in_currency"].get("eur"),
                "change_7d": md["price_change_percentage_7d_in_currency"].get("eur"),
                "change_30d": md["price_change_percentage_30d_in_currency"].get("eur"),
            },
        }
        _btc_ticker_cache = result
        _btc_ticker_ts = time.time()
        return result
    except Exception as e:
        if _btc_ticker_cache:
            return _btc_ticker_cache  # serve stale on error
        raise HTTPException(503, f"BTC price unavailable: {e}")


# ── Chat ─────────────────────────────────────────────────────────────────────

@router.get("/api/chat/messages")
async def get_chat_messages(limit: int = 100):
    return cache.get_chat_messages(limit=limit)


@router.post("/api/chat/send")
async def send_chat_message(body: ChatSendIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "text must not be empty")
    reply, user_msg_id = await telegram.generate_reply(text)
    return {"ok": True, "reply": reply, "user_msg_id": user_msg_id}


# ── Push notifications ────────────────────────────────────────────────────────

@router.post("/api/push/register")
async def register_push_token(body: PushRegisterIn):
    token = body.token.strip()
    if not token:
        raise HTTPException(400, "token must not be empty")
    existing_json = cache.get_setting("push_device_tokens")
    tokens: list = json.loads(existing_json) if existing_json else []
    if token not in tokens:
        tokens.append(token)
        cache.set_setting("push_device_tokens", json.dumps(tokens))
    return {"ok": True}


# ── Private markets ───────────────────────────────────────────────────────────

@router.get("/api/private-markets")
def get_private_markets():
    return cache.get_private_companies()


@router.post("/api/private-markets")
def upsert_private_market(body: PrivateCompanyIn):
    return cache.upsert_private_company(body.model_dump())


@router.patch("/api/private-markets/{company_id}/secondary-price")
def update_secondary_price(company_id: int, body: SecondaryPriceIn):
    cache.set_secondary_price(company_id, body.price)
    return {"ok": True}


@router.delete("/api/private-markets/{company_id}")
def delete_private_market(company_id: int):
    cache.delete_private_company(company_id)
    return {"ok": True}


# ── Analysis ──────────────────────────────────────────────────────────────────

@router.post("/api/analyze")
async def analyze(universe: str = Query("miners")):
    """Fetch latest data and run AI analysis for all tickers."""
    base_tickers, _, _ = get_tickers_for_universe(universe)
    active_tickers = cache.get_active_tickers(base_tickers)

    if universe == "miners":
        try:
            await fetch_btc_prices()
        except Exception as e:
            logger.warning(f"BTC price fetch failed, using cache (non-fatal): {e}")

    try:
        await refresh_all(active_tickers)
    except Exception as e:
        logger.warning(f"Stock price fetch failed, using cache (non-fatal): {e}")

    signals = add_relative_strength({ticker: compute_signals(ticker) for ticker in active_tickers})

    fundamentals = None
    if universe == "miners":
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
        results = await run_analysis(signals, fundamentals, macro, universe=universe)
    except Exception as e:
        raise HTTPException(502, f"AI analysis failed: {e}")

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

    try:
        await telegram.notify_signals(results)
    except Exception as e:
        logger.warning(f"Telegram notification failed (non-fatal): {e}")

    macro_bias = cache.get_setting("macro_bias")
    return {"tickers": results, "fundamentals": fundamentals, "macro": macro, "macro_bias": macro_bias}


@router.get("/api/signals")
async def get_signals(universe: str = Query("miners")):
    """Return current computed signals from cached data (no API calls)."""
    base_tickers, _, _ = get_tickers_for_universe(universe)
    active_tickers = cache.get_active_tickers(base_tickers)
    return add_relative_strength({ticker: compute_signals(ticker) for ticker in active_tickers})


# ── Settings ──────────────────────────────────────────────────────────────────

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
def get_ticker_universe(universe: str = Query("miners")):
    """Return the full ticker universe grouped by category and the current active list."""
    base_tickers, universe_dict, _ = get_tickers_for_universe(universe)
    active = cache.get_active_tickers(base_tickers)
    return {"universe": universe_dict, "active": active}


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
async def get_history(ticker: str, universe: str = Query("miners")):
    base_tickers, _, universe_flat = get_tickers_for_universe(universe)
    active_tickers = cache.get_active_tickers(base_tickers)
    all_valid = set(active_tickers) | set(universe_flat)
    if ticker.upper() not in all_valid:
        raise HTTPException(404, f"Unknown ticker: {ticker}")
    return cache.get_analysis_history(ticker.upper())


@router.get("/api/export/status")
async def export_status():
    configured = google_workspace.is_configured()
    return {
        "configured": configured,
        "missing": google_workspace._get_missing() if not configured else [],
    }


# ── Portfolio ─────────────────────────────────────────────────────────────────

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


# ── Trades ────────────────────────────────────────────────────────────────────

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
    active_miners = cache.get_active_tickers(TICKERS)
    active_tech = cache.get_active_tickers(TECH_TICKERS)
    active_all = set(active_miners) | set(active_tech)
    if body.ticker not in active_all:
        if body.ticker in TICKER_UNIVERSE_FLAT:
            cache.add_active_ticker(body.ticker, TICKERS)
        elif body.ticker in TECH_TICKER_UNIVERSE_FLAT:
            cache.add_active_ticker(body.ticker, TECH_TICKERS)
        if body.ticker in TICKER_UNIVERSE_FLAT or body.ticker in TECH_TICKER_UNIVERSE_FLAT:
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


# ── Cash ──────────────────────────────────────────────────────────────────────

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


# ── Google Sheets export ──────────────────────────────────────────────────────

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
