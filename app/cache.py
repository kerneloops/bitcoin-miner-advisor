import json
import sqlite3
from contextvars import ContextVar
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "cache.db"

# Per-request (per-async-task) user identity set by AuthMiddleware
_current_user_id: ContextVar[str] = ContextVar("user_id", default="")


def get_current_user_id() -> str:
    return _current_user_id.get()


def set_current_user_id(uid: str) -> None:
    _current_user_id.set(uid)


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def init_db():
    with get_conn() as conn:
        # ── Shared tables (no user_id) ──────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                ticker  TEXT,
                date    TEXT,
                open    REAL,
                high    REAL,
                low     REAL,
                close   REAL,
                volume  INTEGER,
                PRIMARY KEY (ticker, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date       TEXT,
                ticker         TEXT,
                signals        TEXT,
                recommendation TEXT,
                reasoning      TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_signals (
                date TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS support_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                email      TEXT NOT NULL,
                message    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # ── User-scoped tables (new installs get user_id from the start) ────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS holdings (
                user_id  TEXT NOT NULL DEFAULT '',
                ticker   TEXT NOT NULL,
                shares   REAL NOT NULL,
                avg_cost REAL NOT NULL,
                PRIMARY KEY (user_id, ticker)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL DEFAULT '',
                ticker     TEXT NOT NULL,
                date       TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                price      REAL NOT NULL,
                quantity   REAL NOT NULL,
                notes      TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id TEXT NOT NULL DEFAULT '',
                key     TEXT NOT NULL,
                value   TEXT,
                PRIMARY KEY (user_id, key)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT '',
                role    TEXT NOT NULL,
                text    TEXT NOT NULL,
                ts      TEXT NOT NULL
            )
        """)

        # ── Migrations for existing installs that lack user_id ──────────────
        # Drop any leftover temp tables from a previously interrupted migration
        conn.execute("DROP TABLE IF EXISTS holdings_new")
        conn.execute("DROP TABLE IF EXISTS settings_new")

        holdings_cols = _table_columns(conn, "holdings")
        if "user_id" not in holdings_cols:
            conn.execute("""
                CREATE TABLE holdings_new (
                    user_id  TEXT NOT NULL DEFAULT '',
                    ticker   TEXT NOT NULL,
                    shares   REAL NOT NULL,
                    avg_cost REAL NOT NULL,
                    PRIMARY KEY (user_id, ticker)
                )
            """)
            conn.execute(
                "INSERT INTO holdings_new (user_id, ticker, shares, avg_cost)"
                " SELECT '', ticker, shares, avg_cost FROM holdings"
            )
            conn.execute("DROP TABLE holdings")
            conn.execute("ALTER TABLE holdings_new RENAME TO holdings")

        settings_cols = _table_columns(conn, "settings")
        if "user_id" not in settings_cols:
            conn.execute("""
                CREATE TABLE settings_new (
                    user_id TEXT NOT NULL DEFAULT '',
                    key     TEXT NOT NULL,
                    value   TEXT,
                    PRIMARY KEY (user_id, key)
                )
            """)
            conn.execute(
                "INSERT INTO settings_new (user_id, key, value)"
                " SELECT '', key, value FROM settings"
            )
            conn.execute("DROP TABLE settings")
            conn.execute("ALTER TABLE settings_new RENAME TO settings")

        trades_cols = _table_columns(conn, "trades")
        if "user_id" not in trades_cols:
            conn.execute(
                "ALTER TABLE trades ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id)"
            )

        chat_cols = _table_columns(conn, "chat_messages")
        if "user_id" not in chat_cols:
            conn.execute(
                "ALTER TABLE chat_messages ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
            )

        # ── One-time cash_balance backfill for user_id='' ───────────────────
        has_cash = conn.execute(
            "SELECT 1 FROM settings WHERE user_id = '' AND key = 'cash_balance'"
        ).fetchone()
        if not has_cash:
            rows = conn.execute(
                "SELECT trade_type, price, quantity FROM trades WHERE user_id = ''"
            ).fetchall()
            balance = 0.0
            for r in rows:
                if r["trade_type"] == "SELL":
                    balance += r["price"] * r["quantity"]
                elif r["trade_type"] == "BUY":
                    balance -= r["price"] * r["quantity"]
            conn.execute(
                "INSERT INTO settings (user_id, key, value) VALUES ('', 'cash_balance', ?)"
                " ON CONFLICT(user_id, key) DO NOTHING",
                (str(round(balance, 2)),),
            )


# ── Shared / price functions (no user filtering) ────────────────────────────

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


# ── User-scoped functions ────────────────────────────────────────────────────

def upsert_holding(ticker: str, shares: float, avg_cost: float):
    uid = _current_user_id.get()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO holdings (user_id, ticker, shares, avg_cost)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, ticker) DO UPDATE SET
                   shares=excluded.shares, avg_cost=excluded.avg_cost""",
            (uid, ticker, shares, avg_cost),
        )


def delete_holding(ticker: str):
    uid = _current_user_id.get()
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM holdings WHERE user_id = ? AND ticker = ?", (uid, ticker)
        )


def delete_ticker_trades(ticker: str):
    """Remove all trades and the holding for a ticker; reverse their cash effects."""
    uid = _current_user_id.get()
    with get_conn() as conn:
        trades = conn.execute(
            "SELECT trade_type, price, quantity FROM trades WHERE user_id = ? AND ticker = ?",
            (uid, ticker),
        ).fetchall()
        conn.execute(
            "DELETE FROM trades WHERE user_id = ? AND ticker = ?", (uid, ticker)
        )
        conn.execute(
            "DELETE FROM holdings WHERE user_id = ? AND ticker = ?", (uid, ticker)
        )
        for t in trades:
            _adjust_cash(conn, t["trade_type"], t["price"], t["quantity"], sign=-1)


def get_holdings() -> list[dict]:
    uid = _current_user_id.get()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE user_id = ? ORDER BY ticker", (uid,)
        ).fetchall()
    return [dict(r) for r in rows]


def _recompute_holding(conn, ticker: str):
    uid = _current_user_id.get()

    # Auto-migrate legacy position entered before the trade log was source of truth
    has_buy = conn.execute(
        "SELECT 1 FROM trades WHERE user_id = ? AND ticker = ? AND trade_type = 'BUY' LIMIT 1",
        (uid, ticker),
    ).fetchone()
    if not has_buy:
        existing = conn.execute(
            "SELECT shares, avg_cost FROM holdings WHERE user_id = ? AND ticker = ?",
            (uid, ticker),
        ).fetchone()
        if existing and existing["shares"] > 0:
            from datetime import date as _date, timedelta
            first_trade = conn.execute(
                "SELECT MIN(date) as d FROM trades WHERE user_id = ? AND ticker = ?",
                (uid, ticker),
            ).fetchone()
            if first_trade and first_trade["d"]:
                deposit_date = (
                    _date.fromisoformat(first_trade["d"]) - timedelta(days=1)
                ).isoformat()
            else:
                deposit_date = _date.today().isoformat()
            conn.execute(
                "INSERT INTO trades (user_id, ticker, date, trade_type, price, quantity, notes)"
                " VALUES (?, ?, ?, 'BUY', ?, ?, 'initial position (auto-migrated)')",
                (uid, ticker, deposit_date, existing["avg_cost"], existing["shares"]),
            )

    trades = conn.execute(
        "SELECT * FROM trades WHERE user_id = ? AND ticker = ? ORDER BY date ASC, id ASC",
        (uid, ticker),
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
        conn.execute(
            "DELETE FROM holdings WHERE user_id = ? AND ticker = ?", (uid, ticker)
        )
    else:
        conn.execute(
            """INSERT INTO holdings (user_id, ticker, shares, avg_cost)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, ticker) DO UPDATE SET
                   shares=excluded.shares, avg_cost=excluded.avg_cost""",
            (uid, ticker, round(shares, 8), round(avg_cost, 4)),
        )


def get_cash() -> float:
    val = get_setting("cash_balance", "0")
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def set_cash(amount: float) -> None:
    set_setting("cash_balance", str(round(amount, 2)))


def _adjust_cash(conn, trade_type: str, price: float, quantity: float, sign: int = 1):
    """Update cash_balance: +proceeds for SELL, -cost for BUY. Reverse with sign=-1."""
    uid = _current_user_id.get()
    row = conn.execute(
        "SELECT value FROM settings WHERE user_id = ? AND key = 'cash_balance'", (uid,)
    ).fetchone()
    current = float(row["value"]) if row else 0.0
    if trade_type == "BUY":
        current -= sign * price * quantity
    elif trade_type == "SELL":
        current += sign * price * quantity
    conn.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, 'cash_balance', ?)"
        " ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
        (uid, str(round(current, 2))),
    )


def add_trade(ticker: str, date: str, trade_type: str, price: float, quantity: float, notes: str = ""):
    uid = _current_user_id.get()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (user_id, ticker, date, trade_type, price, quantity, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, ticker, date, trade_type, price, quantity, notes),
        )
        _recompute_holding(conn, ticker)
        _adjust_cash(conn, trade_type, price, quantity, sign=1)


def delete_trade(trade_id: int):
    uid = _current_user_id.get()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ticker, trade_type, price, quantity FROM trades WHERE id = ? AND user_id = ?",
            (trade_id, uid),
        ).fetchone()
        if row:
            ticker = row["ticker"]
            conn.execute(
                "DELETE FROM trades WHERE id = ? AND user_id = ?", (trade_id, uid)
            )
            _recompute_holding(conn, ticker)
            _adjust_cash(conn, row["trade_type"], row["price"], row["quantity"], sign=-1)


def get_trades() -> list[dict]:
    uid = _current_user_id.get()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_setting(key: str, default: str | None = None) -> str | None:
    uid = _current_user_id.get()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE user_id = ? AND key = ?", (uid, key)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    uid = _current_user_id.get()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (uid, key, value),
        )


def get_active_tickers(default: list[str]) -> list[str]:
    val = get_setting("active_tickers")
    if val:
        try:
            return json.loads(val)
        except Exception:
            pass
    return list(default)


def add_active_ticker(ticker: str, default: list[str]) -> None:
    current = get_active_tickers(default)
    if ticker not in current:
        current.append(ticker)
        set_setting("active_tickers", json.dumps(current))


def get_all_holdings() -> dict:
    """Return holdings keyed by ticker: {ticker: shares}."""
    uid = _current_user_id.get()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker, shares FROM holdings WHERE user_id = ?", (uid,)
        ).fetchall()
    return {r["ticker"]: r["shares"] for r in rows}


def add_chat_message(role: str, text: str) -> int:
    """Insert a chat message and return its row id."""
    uid = _current_user_id.get()
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages (user_id, role, text, ts) VALUES (?, ?, ?, ?)",
            (uid, role, text, ts),
        )
        return cur.lastrowid


def get_chat_messages(limit: int = 100) -> list[dict]:
    uid = _current_user_id.get()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, text, ts FROM chat_messages"
            " WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (uid, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


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


