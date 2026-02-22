import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "cache.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                ticker TEXT,
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                PRIMARY KEY (ticker, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT,
                ticker TEXT,
                signals TEXT,
                recommendation TEXT,
                reasoning TEXT
            )
        """)


def upsert_prices(ticker: str, rows: list[dict]):
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, volume)
               VALUES (:ticker, :date, :open, :high, :low, :close, :volume)""",
            [{"ticker": ticker, **r} for r in rows],
        )


def get_prices(ticker: str, limit: int = 365) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_latest_date(ticker: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) as d FROM prices WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row["d"] if row and row["d"] else None


def save_analysis(run_date: str, ticker: str, signals: dict, recommendation: str, reasoning: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO analyses (run_date, ticker, signals, recommendation, reasoning)
               VALUES (?, ?, ?, ?, ?)""",
            (run_date, ticker, json.dumps(signals), recommendation, reasoning),
        )


def get_price_on_or_after(ticker: str, target_date: str) -> float | None:
    """Return closing price on or after target_date (handles weekends/holidays)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date >= ? ORDER BY date ASC LIMIT 1",
            (ticker, target_date),
        ).fetchone()
    return float(row["close"]) if row else None


def get_analysis_history(ticker: str, limit: int = 12) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE ticker = ? ORDER BY run_date DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
    today = date.today().isoformat()
    result = []
    for r in rows:
        d = dict(r)
        d["signals"] = json.loads(d["signals"])

        target_date = (date.fromisoformat(d["run_date"]) + timedelta(days=14)).isoformat()
        entry_price = d["signals"].get("current_price")

        if not entry_price or target_date > today:
            d["outcome_return_pct"] = None
            d["outcome"] = "pending"
        else:
            exit_price = get_price_on_or_after(ticker, target_date)
            if exit_price is None:
                d["outcome_return_pct"] = None
                d["outcome"] = "pending"
            else:
                ret = round((exit_price / entry_price - 1) * 100, 2)
                d["outcome_return_pct"] = ret
                rec = d["recommendation"]
                if rec == "BUY":
                    correct = ret > 0
                elif rec == "SELL":
                    correct = ret < 0
                else:  # HOLD
                    correct = -5.0 <= ret <= 5.0
                d["outcome"] = "correct" if correct else "incorrect"

        result.append(d)
    return result
