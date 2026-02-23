import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

load_dotenv()

from app import cache
from app.auth import verify_token
from app.routes import router


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Always allow login page, login POST, static assets, and Telegram webhook
        if path in ("/login", "/logout", "/api/telegram/webhook") or path.startswith("/static/"):
            return await call_next(request)
        if not verify_token(request.cookies.get("session")):
            return RedirectResponse(url="/login", status_code=302)
        return await call_next(request)

logger = logging.getLogger(__name__)


async def _scheduled_analysis():
    from app.advisor import run_analysis
    from app.data import TICKERS, fetch_btc_prices, refresh_all
    from app.macro import fetch_all_macro
    from app.miners import fetch_miner_fundamentals
    from app.technicals import add_relative_strength, compute_signals

    logger.info("Scheduled analysis starting…")
    try:
        await fetch_btc_prices()
        await refresh_all()
    except Exception as e:
        logger.error(f"Scheduled fetch failed: {e}")
        return

    signals = add_relative_strength({ticker: compute_signals(ticker) for ticker in TICKERS})

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
        from app import cache, sizing, telegram

        results = await run_analysis(signals, fundamentals, macro)

        # Attach position guidance
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

        # Send Telegram notifications
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


app = FastAPI(title="Hash & Burn", lifespan=lifespan)
app.add_middleware(AuthMiddleware)

cache.init_db()
app.include_router(router)

frontend = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend), name="static")


@app.get("/")
async def index():
    return FileResponse(frontend / "index.html")
