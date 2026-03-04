import asyncio
import logging
from datetime import date, timedelta

from . import cache
from .advisor import get_recommendation
from .data import classify_ticker
from .technicals import compute_signals

logger = logging.getLogger(__name__)

_backfill_state: dict = {
    "running": False,
    "total": 0,
    "completed": 0,
    "errors": 0,
    "current_ticker": None,
    "current_date": None,
}


def get_backfill_status() -> dict:
    return dict(_backfill_state)


def _trading_days(days_back: int) -> list[str]:
    """Return past N calendar days that are weekdays (Mon-Fri), oldest first."""
    result = []
    d = date.today() - timedelta(days=1)  # start from yesterday
    checked = 0
    while checked < days_back * 2 and len(result) < days_back:
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            result.append(d.isoformat())
        d -= timedelta(days=1)
        checked += 1
    result.reverse()
    return result


def _btc_trend_from_prices(btc_rows: list[dict]) -> str:
    """Compute BTC trend summary from price rows (same logic as advisor._btc_trend_summary)."""
    if len(btc_rows) < 7:
        return "unavailable"
    week_ago = float(btc_rows[-7]["close"])
    now = float(btc_rows[-1]["close"])
    pct = round((now / week_ago - 1) * 100, 2)
    return f"{pct:+.1f}% over 7 days (current: ${now:,.0f})"


async def run_backfill(tickers: list[str], days_back: int = 60):
    """Backfill historical analyses for all tickers over past N trading days."""
    global _backfill_state

    if _backfill_state["running"]:
        return

    days = _trading_days(days_back)

    # Build work list, skipping dates that already have analyses
    work = []
    for d in days:
        for t in tickers:
            if not cache.has_analysis(t, d):
                work.append((t, d))

    _backfill_state = {
        "running": True,
        "total": len(work),
        "completed": 0,
        "errors": 0,
        "current_ticker": None,
        "current_date": None,
    }

    if not work:
        _backfill_state["running"] = False
        return

    sem = asyncio.Semaphore(5)

    async def _process(ticker: str, run_date: str):
        async with sem:
            _backfill_state["current_ticker"] = ticker
            _backfill_state["current_date"] = run_date
            try:
                signals = compute_signals(ticker, as_of_date=run_date)
                if "error" in signals:
                    _backfill_state["errors"] += 1
                    _backfill_state["completed"] += 1
                    return

                btc_rows = cache.get_prices_as_of("BTC", run_date, limit=10)
                btc_trend = _btc_trend_from_prices(btc_rows)

                category = classify_ticker(ticker)
                rec = await get_recommendation(
                    ticker, signals, btc_trend,
                    fundamentals=None, macro=None,
                    ticker_category=category,
                )

                cache.save_analysis(
                    run_date, ticker, signals,
                    rec["recommendation"], rec.get("reasoning", ""),
                    confidence=rec.get("confidence"),
                    key_risk=rec.get("key_risk"),
                    is_backfill=True,
                )
            except Exception as e:
                logger.warning(f"Backfill error {ticker} {run_date}: {e}")
                _backfill_state["errors"] += 1
            finally:
                _backfill_state["completed"] += 1

    # Process sequentially to avoid overwhelming the AI API
    for ticker, run_date in work:
        await _process(ticker, run_date)

    _backfill_state["running"] = False
    _backfill_state["current_ticker"] = None
    _backfill_state["current_date"] = None
