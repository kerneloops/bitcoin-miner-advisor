import json
import sqlite3
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


def get_analysis_history(ticker: str, limit: int = 12) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE ticker = ? ORDER BY run_date DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["signals"] = json.loads(d["signals"])
        result.append(d)
    return result
