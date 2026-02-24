import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

load_dotenv()

from app import cache
from app import users as _users
from app.routes import router

_PUBLIC_PATHS = {
    "/login",
    "/logout",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/me",
    "/api/auth/logout",
    "/api/support",
    "/api/telegram/webhook",
}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject state-changing API requests whose Origin doesn't match the host."""
    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    async def dispatch(self, request: Request, call_next):
        if request.method in self._SAFE_METHODS:
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        # Auth endpoints are rate-limited separately; skip CSRF for them
        _csrf_exempt = {"/api/auth/login", "/api/auth/register",
                        "/api/auth/logout", "/api/telegram/webhook"}
        if request.url.path in _csrf_exempt:
            return await call_next(request)
        origin = request.headers.get("Origin") or request.headers.get("Referer", "")
        host = request.headers.get("Host", "")
        if origin and host and host not in origin:
            return JSONResponse({"detail": "CSRF check failed"}, status_code=403)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        token = request.cookies.get("session") or request.headers.get("X-Session-Token")
        user = _users.get_session(token)
        if not user:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            return RedirectResponse(url="/login", status_code=302)

        cache.set_current_user_id(user["user_id"])
        return await call_next(request)


logger = logging.getLogger(__name__)


async def _scheduled_analysis():
    from app.advisor import run_analysis
    from app.data import TICKERS, fetch_btc_prices, refresh_all
    from app.macro import fetch_all_macro
    from app.miners import fetch_miner_fundamentals
    from app.technicals import add_relative_strength, compute_signals

    primary = _users.get_primary_user_id()
    if primary:
        cache.set_current_user_id(primary)

    logger.info("Scheduled analysis starting…")
    active_tickers = cache.get_active_tickers(TICKERS)
    try:
        await fetch_btc_prices()
        await refresh_all(active_tickers)
    except Exception as e:
        logger.error(f"Scheduled fetch failed: {e}")
        return

    signals = add_relative_strength({ticker: compute_signals(ticker) for ticker in active_tickers})

    fundamentals = None
    try:
        btc_rows = cache.get_prices("BTC", limit=2)
        btc_price = float(btc_rows[-1]["close"]) if btc_rows else 0
        fundamentals = await fetch_miner_fundamentals(btc_price)
    except Exception as e:
        logger.warning(f"Scheduled fundamentals fetch failed (non-fatal): {e}")

    macro = None
    try:
        macro = await fetch_all_macro()
    except Exception as e:
        logger.warning(f"Scheduled macro fetch failed (non-fatal): {e}")

    try:
        from app import sizing, telegram

        results = await run_analysis(signals, fundamentals, macro)

        tier_name = cache.get_setting("risk_tier", "neutral")
        cap_str = cache.get_setting("total_capital")
        total_capital = float(cap_str) if cap_str else None
        holdings = cache.get_all_holdings()
        for ticker, d in results.items():
            try:
                d["position_guidance"] = sizing.compute_guidance(
                    ticker=ticker,
                    rec=d.get("recommendation"),
                    confidence=d.get("confidence"),
                    price=d.get("current_price"),
                    shares_held=holdings.get(ticker, 0),
                    tier_name=tier_name,
                    total_capital=total_capital,
                )
            except Exception:
                d["position_guidance"] = None

        try:
            await telegram.notify_signals(results)
        except Exception as e:
            logger.warning(f"Scheduled Telegram notification failed (non-fatal): {e}")

        logger.info("Scheduled analysis complete.")
    except Exception as e:
        logger.error(f"Scheduled AI analysis failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    schedule_time = os.getenv("ANALYSIS_SCHEDULE_TIME", "").strip()
    scheduler = None
    if schedule_time:
        try:
            hour, minute = schedule_time.split(":")
            scheduler = AsyncIOScheduler()
            scheduler.add_job(
                _scheduled_analysis,
                CronTrigger(hour=int(hour), minute=int(minute)),
                id="daily_analysis",
                replace_existing=True,
            )
            scheduler.start()
            logger.info(f"Daily analysis scheduled at {schedule_time}")
        except Exception as e:
            logger.warning(f"Failed to start scheduler: {e}")

    from app.telegram import setup_webhook
    try:
        await setup_webhook()
    except Exception as e:
        logger.warning(f"Telegram webhook setup failed (non-fatal): {e}")

    yield

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)


from app.routes import _limiter

app = FastAPI(title="Hash & Burn", lifespan=lifespan)
app.state.limiter = _limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(AuthMiddleware)

_users.init_users_db()
cache.init_db()
cache.init_private_companies()
app.include_router(router)

frontend = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend), name="static")


@app.get("/")
async def index():
    return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
