import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

_limiter = Limiter(key_func=get_remote_address)

from .advisor import run_analysis
from .billing import (
    HASHRATE_PRESET_TICKERS, TIER_PRICES, get_tier_limits, get_tier_level,
    require_tier, create_checkout_session, create_portal_session,
    handle_webhook_event,
)
from .data import (
    DEFAULT_TICKERS, TICKER_UNIVERSE, TICKER_UNIVERSE_FLAT,
    BENCHMARK_TICKER, fetch_btc_prices, refresh_all, refresh_benchmark, refresh_ticker,
    classify_ticker, search_tickers,
)
from .macro import fetch_all_macro
from .miners import fetch_miner_fundamentals
from .backfill import get_backfill_status, run_backfill
from .technicals import add_relative_strength, compute_signals
from . import cache, google_workspace, push, sizing, telegram
from . import users as user_store

router = APIRouter()
logger = logging.getLogger(__name__)

_frontend = Path(__file__).parent.parent / "frontend"


# ── Auth pages ───────────────────────────────────────────────────────────────

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}

@router.get("/login")
def login_page():
    return FileResponse(_frontend / "login.html", headers=_NO_CACHE)


@router.get("/tech")
def tech_page():
    return RedirectResponse(url="/", status_code=301)


@router.post("/login")
@_limiter.limit("10/minute")
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
            httponly=True, secure=True, samesite="strict",
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
@_limiter.limit("10/minute")
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
        httponly=True, secure=True, samesite="strict",
        max_age=30 * 24 * 3600,
    )
    return response


@router.post("/api/auth/register")
@_limiter.limit("5/minute")
async def api_register(body: AuthRegisterIn, request: Request):
    max_users = int(os.getenv("MAX_USERS", "20"))
    was_first = user_store.is_first_user()
    try:
        result = user_store.create_user(body.username, body.password, max_users)
    except user_store.RegistrationError as e:
        if e.code == "beta_full":
            raise HTTPException(403, "beta_full")
        elif e.code == "username_taken":
            # Generic 400 — don't reveal whether the username exists
            raise HTTPException(400, "registration_failed")
        elif e.code in ("password_too_short", "password_too_weak"):
            raise HTTPException(422, e.code)
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
        httponly=True, secure=True, samesite="strict",
        max_age=30 * 24 * 3600,
    )
    return response


@router.get("/api/auth/me")
async def api_me(request: Request):
    token = request.cookies.get("session") or request.headers.get("X-Session-Token")
    user = user_store.get_session(token)
    if not user:
        raise HTTPException(401, "Unauthorized")
    primary_id = user_store.get_primary_user_id()
    tier = user_store.get_user_tier(user["user_id"])
    return {
        "username": user["username"],
        "user_id": user["user_id"],
        "is_admin": user["user_id"] == primary_id,
        "tier": tier,
    }


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


def _current_user_tier() -> str:
    """Return the effective subscription tier for the current user."""
    uid = cache.get_current_user_id()
    if not uid:
        return "expired"
    return user_store.get_user_tier(uid)


# ── Subscription / Billing ───────────────────────────────────────────────────

@router.get("/api/subscription")
def get_subscription():
    """Return current user's subscription info."""
    uid = cache.get_current_user_id()
    if not uid:
        raise HTTPException(401, "Unauthorized")
    user = user_store.get_user_by_id(uid)
    if not user:
        raise HTTPException(401, "Unauthorized")
    tier = user_store.get_user_tier(uid)
    limits = get_tier_limits(tier)
    return {
        "tier": tier,
        "status": user.get("subscription_status", "trialing"),
        "trial_ends_at": user.get("trial_ends_at"),
        "subscription_ends_at": user.get("subscription_ends_at"),
        "limits": limits,
        "stripe_subscription_id": user.get("stripe_subscription_id"),
    }


@router.get("/api/pricing")
def get_pricing():
    """Return tier info and Stripe price IDs for frontend."""
    return {
        "tiers": TIER_PRICES,
        "stripe_publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        "price_ids": {
            "hashrate_monthly": os.getenv("STRIPE_PRICE_HASHRATE_MONTHLY", ""),
            "hashrate_annual": os.getenv("STRIPE_PRICE_HASHRATE_ANNUAL", ""),
            "blockrate_monthly": os.getenv("STRIPE_PRICE_BLOCKRATE_MONTHLY", ""),
            "blockrate_annual": os.getenv("STRIPE_PRICE_BLOCKRATE_ANNUAL", ""),
            "difficulty_monthly": os.getenv("STRIPE_PRICE_DIFFICULTY_MONTHLY", ""),
            "difficulty_annual": os.getenv("STRIPE_PRICE_DIFFICULTY_ANNUAL", ""),
        },
    }


class CheckoutIn(BaseModel):
    price_id: str


@router.post("/api/billing/checkout")
def billing_checkout(body: CheckoutIn, request: Request):
    """Create a Stripe Checkout session."""
    uid = cache.get_current_user_id()
    if not uid:
        raise HTTPException(401, "Unauthorized")
    base_url = os.getenv("APP_BASE_URL", "https://lapio.dev")
    try:
        url = create_checkout_session(
            user_id=uid,
            price_id=body.price_id,
            success_url=f"{base_url}/?checkout=success",
            cancel_url=f"{base_url}/pricing?checkout=cancel",
        )
    except Exception as e:
        logger.error(f"Stripe checkout failed: {e}")
        raise HTTPException(502, f"Checkout failed: {e}")
    return {"url": url}


@router.post("/api/billing/portal")
def billing_portal():
    """Create a Stripe Customer Portal session."""
    uid = cache.get_current_user_id()
    if not uid:
        raise HTTPException(401, "Unauthorized")
    base_url = os.getenv("APP_BASE_URL", "https://lapio.dev")
    try:
        url = create_portal_session(uid, return_url=base_url)
    except Exception as e:
        logger.error(f"Stripe portal failed: {e}")
        raise HTTPException(502, f"Portal failed: {e}")
    return {"url": url}


@router.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        result = handle_webhook_event(payload, sig)
        return result
    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        raise HTTPException(400, f"Webhook error: {e}")


@router.get("/pricing")
def pricing_page():
    return FileResponse(_frontend / "pricing.html", headers=_NO_CACHE)


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
    trading_style: str | None = None
    rsi_overbought: int | None = None
    rsi_oversold: int | None = None


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
    tier = _current_user_tier()
    require_tier(tier, 2, "chat")
    limits = get_tier_limits(tier)

    # Enforce daily chat limit (0 = disabled, 999 = unlimited)
    if limits["chat_daily"] < 999:
        uid = cache.get_current_user_id()
        count = user_store.increment_chat_count(uid)
        if count > limits["chat_daily"]:
            raise HTTPException(429, detail={
                "code": "chat_limit_reached",
                "limit": limits["chat_daily"],
                "current_tier": tier,
            })

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
    require_tier(_current_user_tier(), 3, "private_markets_edit")
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
async def analyze():
    """Fetch latest data and run AI analysis for all tickers."""
    tier = _current_user_tier()
    require_tier(tier, 1, "analysis")
    limits = get_tier_limits(tier)

    active_tickers = cache.get_active_tickers(DEFAULT_TICKERS)
    # Clip to allowed ticker count
    if len(active_tickers) > limits["max_tickers"]:
        active_tickers = active_tickers[:limits["max_tickers"]]

    has_crypto = any(classify_ticker(t) == "crypto" for t in active_tickers)

    if has_crypto:
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
    if has_crypto:
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

    signal_prefs = {
        "trading_style": cache.get_setting("trading_style", "balanced"),
        "rsi_overbought": int(cache.get_setting("rsi_overbought", "70") or "70"),
        "rsi_oversold": int(cache.get_setting("rsi_oversold", "30") or "30"),
    }
    try:
        results = await run_analysis(signals, fundamentals, macro, signal_prefs=signal_prefs)
    except Exception as e:
        raise HTTPException(502, f"AI analysis failed: {e}")

    tier_name = cache.get_setting("risk_tier", "neutral")
    cap_str = cache.get_setting("total_capital")
    total_capital = float(cap_str) if cap_str else None
    holdings = cache.get_all_holdings()

    # Position sizing only for tier >= 2 (blockrate+)
    skip_sizing = get_tier_level(tier) < 2

    for ticker, d in results.items():
        if skip_sizing:
            d["position_guidance"] = None
            continue
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


@router.get("/api/accuracy")
def get_accuracy():
    """Return aggregate accuracy stats per window."""
    active_tickers = cache.get_active_tickers(DEFAULT_TICKERS)
    return cache.get_accuracy_summary(active_tickers)


class BackfillIn(BaseModel):
    days_back: int = 60


@router.post("/api/backfill")
async def start_backfill(body: BackfillIn):
    """Launch backfill of historical analyses."""
    import asyncio
    status = get_backfill_status()
    if status["running"]:
        return {"ok": False, "message": "Backfill already running", "status": status}
    active_tickers = cache.get_active_tickers(DEFAULT_TICKERS)
    days = max(1, min(body.days_back, 180))
    asyncio.create_task(run_backfill(active_tickers, days_back=days))
    return {"ok": True}


@router.get("/api/backfill/status")
def backfill_status():
    """Return current backfill progress."""
    return get_backfill_status()


@router.get("/api/signals")
async def get_signals():
    """Return current computed signals from cached data (no API calls)."""
    active_tickers = cache.get_active_tickers(DEFAULT_TICKERS)
    return add_relative_strength({ticker: compute_signals(ticker) for ticker in active_tickers})


@router.get("/api/latest-analysis")
def latest_analysis():
    """Return the most recent stored analysis per ticker (no API calls)."""
    active_tickers = cache.get_active_tickers(DEFAULT_TICKERS)
    tickers = cache.get_latest_analysis(active_tickers)
    if not tickers:
        return {"tickers": None}
    macro = cache.get_latest_macro() or {}
    macro_bias = cache.get_setting("macro_bias")
    return {"tickers": tickers, "macro": macro, "macro_bias": macro_bias}


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/api/settings")
def get_settings():
    return {
        "risk_tier": cache.get_setting("risk_tier", "neutral"),
        "total_capital": float(cache.get_setting("total_capital", "0") or "0"),
        "trading_style": cache.get_setting("trading_style", "balanced"),
        "rsi_overbought": int(cache.get_setting("rsi_overbought", "70") or "70"),
        "rsi_oversold": int(cache.get_setting("rsi_oversold", "30") or "30"),
        "telegram_configured": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
    }


@router.post("/api/settings")
def save_settings(body: SettingsIn):
    from .advisor import TRADING_STYLES
    if body.risk_tier is not None:
        if body.risk_tier not in sizing.TIERS:
            raise HTTPException(400, f"Invalid risk_tier. Must be one of: {list(sizing.TIERS)}")
        cache.set_setting("risk_tier", body.risk_tier)
    if body.total_capital is not None:
        cache.set_setting("total_capital", str(body.total_capital))
    if body.trading_style is not None:
        if body.trading_style not in TRADING_STYLES:
            raise HTTPException(400, f"Invalid trading_style. Must be one of: {list(TRADING_STYLES)}")
        cache.set_setting("trading_style", body.trading_style)
    if body.rsi_overbought is not None:
        if not 50 <= body.rsi_overbought <= 95:
            raise HTTPException(400, "rsi_overbought must be between 50 and 95")
        cache.set_setting("rsi_overbought", str(body.rsi_overbought))
    if body.rsi_oversold is not None:
        if not 5 <= body.rsi_oversold <= 50:
            raise HTTPException(400, "rsi_oversold must be between 5 and 50")
        cache.set_setting("rsi_oversold", str(body.rsi_oversold))
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
    active = cache.get_active_tickers(DEFAULT_TICKERS)
    # Add "Custom" group for active tickers not in any predefined group
    universe_flat_set = set(TICKER_UNIVERSE_FLAT)
    custom = [t for t in active if t not in universe_flat_set]
    result = dict(TICKER_UNIVERSE)
    if custom:
        result["Custom"] = custom
    tier = _current_user_tier()
    limits = get_tier_limits(tier)
    return {"universe": result, "active": active, "tier": tier, "limits": limits}


class TickerIn(BaseModel):
    ticker: str


@router.post("/api/tickers")
async def add_ticker(body: TickerIn):
    """Add a ticker to the user's active watchlist."""
    tier = _current_user_tier()
    require_tier(tier, 1, "watchlist")
    limits = get_tier_limits(tier)

    ticker = body.ticker.upper().strip()
    if not ticker.isalpha() or len(ticker) > 10:
        raise HTTPException(400, f"Invalid ticker: {ticker}")

    # Hashrate tier: only preset tickers allowed
    if get_tier_level(tier) == 1 and ticker not in HASHRATE_PRESET_TICKERS:
        raise HTTPException(403, detail={
            "code": "preset_only",
            "message": f"Hashrate plan only allows preset tickers: {', '.join(HASHRATE_PRESET_TICKERS)}",
        })

    # Enforce max tickers
    active = cache.get_active_tickers(DEFAULT_TICKERS)
    if len(active) >= limits["max_tickers"] and ticker not in active:
        raise HTTPException(403, detail={
            "code": "ticker_limit_reached",
            "max": limits["max_tickers"],
            "current_tier": tier,
        })

    cache.add_active_ticker(ticker, DEFAULT_TICKERS)
    try:
        await refresh_ticker(ticker)
    except Exception as e:
        logger.warning(f"Price backfill for {ticker} (non-fatal): {e}")
    active = cache.get_active_tickers(DEFAULT_TICKERS)
    return {"active": active}


@router.delete("/api/tickers/{ticker}")
def delete_ticker(ticker: str):
    """Remove a ticker from the user's active watchlist."""
    ticker = ticker.upper()
    remaining = cache.remove_active_ticker(ticker, DEFAULT_TICKERS)
    return {"active": remaining}


@router.get("/api/ticker-search")
async def ticker_search(q: str = Query("")):
    """Search Polygon for tickers matching query."""
    tier = _current_user_tier()
    require_tier(tier, 2, "ticker_search")
    q = q.strip()
    if len(q) < 1:
        return []
    results = await search_tickers(q, limit=8)
    return results


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
async def get_history(ticker: str):
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
    active_all = set(cache.get_active_tickers(DEFAULT_TICKERS))
    if body.ticker not in active_all and body.ticker in TICKER_UNIVERSE_FLAT:
        cache.add_active_ticker(body.ticker, DEFAULT_TICKERS)
        try:
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


# ── Admin ─────────────────────────────────────────────────────────────────────

@router.get("/admin")
def admin_page():
    return FileResponse(_frontend / "admin.html", headers=_NO_CACHE)


@router.get("/api/admin/users")
def admin_list_users():
    primary_id = user_store.get_primary_user_id()
    current_uid = cache.get_current_user_id()
    if current_uid != primary_id:
        raise HTTPException(403, "Admin only")
    return {
        "users": user_store.list_users(),
        "primary_id": primary_id,
        "max_users": int(os.getenv("MAX_USERS", "20")),
    }


@router.post("/api/admin/users/{user_id}/active")
async def admin_set_active(user_id: str, request: Request):
    primary_id = user_store.get_primary_user_id()
    current_uid = cache.get_current_user_id()
    if current_uid != primary_id:
        raise HTTPException(403, "Admin only")
    if user_id == primary_id:
        raise HTTPException(400, "Cannot deactivate your own account")
    body = await request.json()
    user_store.set_user_active(user_id, bool(body.get("is_active", True)))
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/revoke")
def admin_revoke_sessions(user_id: str):
    primary_id = user_store.get_primary_user_id()
    current_uid = cache.get_current_user_id()
    if current_uid != primary_id:
        raise HTTPException(403, "Admin only")
    user_store.revoke_sessions(user_id)
    return {"ok": True}


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
