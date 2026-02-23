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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS holdings (
                ticker TEXT PRIMARY KEY,
                shares REAL NOT NULL,
                avg_cost REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                notes TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_signals (
                date TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
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


def upsert_holding(ticker: str, shares: float, avg_cost: float):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO holdings (ticker, shares, avg_cost)
               VALUES (?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET shares=excluded.shares, avg_cost=excluded.avg_cost""",
            (ticker, shares, avg_cost),
        )


def delete_holding(ticker: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM holdings WHERE ticker = ?", (ticker,))


def delete_ticker_trades(ticker: str):
    """Remove all trades and the holding for a ticker (full position close)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM trades WHERE ticker = ?", (ticker,))
        conn.execute("DELETE FROM holdings WHERE ticker = ?", (ticker,))


def get_holdings() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM holdings ORDER BY ticker").fetchall()
    return [dict(r) for r in rows]


def _recompute_holding(conn, ticker: str):
    trades = conn.execute(
        "SELECT * FROM trades WHERE ticker = ? ORDER BY date ASC, id ASC",
        (ticker,)
    ).fetchall()

    shares = 0.0
    avg_cost = 0.0
    for t in trades:
        if t["trade_type"] == "BUY":
            total_cost = shares * avg_cost + t["quantity"] * t["price"]
            shares += t["quantity"]
            avg_cost = total_cost / shares
        elif t["trade_type"] == "SELL":
            shares = max(0.0, shares - t["quantity"])

    if shares <= 0:
        conn.execute("DELETE FROM holdings WHERE ticker = ?", (ticker,))
    else:
        conn.execute(
            """INSERT INTO holdings (ticker, shares, avg_cost)
               VALUES (?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET shares=excluded.shares, avg_cost=excluded.avg_cost""",
            (ticker, round(shares, 8), round(avg_cost, 4)),
        )


def add_trade(ticker: str, date: str, trade_type: str, price: float, quantity: float, notes: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (ticker, date, trade_type, price, quantity, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, date, trade_type, price, quantity, notes),
        )
        _recompute_holding(conn, ticker)


def delete_trade(trade_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT ticker FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row:
            ticker = row["ticker"]
            conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
            _recompute_holding(conn, ticker)


def get_trades() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY date DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_macro(date_str: str, data: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO macro_signals (date, data) VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET data=excluded.data""",
            (date_str, json.dumps(data)),
        )


def get_latest_macro() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT data FROM macro_signals ORDER BY date DESC LIMIT 1"
        ).fetchone()
    return json.loads(row["data"]) if row else {}


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_all_holdings() -> dict:
    """Return holdings keyed by ticker: {ticker: shares}."""
    with get_conn() as conn:
        rows = conn.execute("SELECT ticker, shares FROM holdings").fetchall()
    return {r["ticker"]: r["shares"] for r in rows}


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
