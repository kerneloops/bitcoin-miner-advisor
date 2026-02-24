import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

USERS_DB_PATH = Path(__file__).parent.parent / "data" / "users.db"


class RegistrationError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _get_conn() -> sqlite3.Connection:
    USERS_DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(USERS_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_users_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         TEXT PRIMARY KEY,
                username   TEXT UNIQUE NOT NULL,
                pw_hash    TEXT NOT NULL,
                pw_salt    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_active  INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                last_seen  TEXT NOT NULL,
                user_agent TEXT NOT NULL DEFAULT ''
            )
        """)


def count_users() -> int:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM users WHERE is_active = 1"
        ).fetchone()
    return row["n"] if row else 0


def is_first_user() -> bool:
    return count_users() == 0


def get_primary_user_id() -> str | None:
    """Return the earliest created active user (used by the scheduler)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE is_active = 1 ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return row["id"] if row else None


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 260_000
    ).hex()


def _validate_password(password: str) -> None:
    if len(password) < 10:
        raise RegistrationError("password_too_short")
    import re
    if not re.search(r'[A-Za-z]', password):
        raise RegistrationError("password_too_weak")
    if not re.search(r'[0-9!@#$%^&*()_+\-=\[\]{};:\'",.<>?/\\|`~]', password):
        raise RegistrationError("password_too_weak")


def create_user(username: str, password: str, max_users: int) -> dict:
    _validate_password(password)
    with _get_conn() as conn:
        current_count = conn.execute(
            "SELECT COUNT(*) as n FROM users WHERE is_active = 1"
        ).fetchone()["n"]
        if current_count >= max_users:
            raise RegistrationError("beta_full")
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            raise RegistrationError("username_taken")
        from datetime import datetime, timezone
        user_id = secrets.token_hex(16)
        salt = secrets.token_hex(16)
        pw_hash = _hash_password(password, salt)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO users (id, username, pw_hash, pw_salt, created_at, is_active)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            (user_id, username, pw_hash, salt, now),
        )
    return {"id": user_id, "username": username}


def verify_password(username: str, password: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, pw_hash, pw_salt FROM users"
            " WHERE username = ? AND is_active = 1",
            (username,),
        ).fetchone()
    if not row:
        return None
    expected = _hash_password(password, row["pw_salt"])
    if not secrets.compare_digest(expected, row["pw_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


def create_session(user_id: str, user_agent: str = "") -> str:
    from datetime import datetime, timezone
    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, last_seen, user_agent)"
            " VALUES (?, ?, ?, ?, ?)",
            (token, user_id, now, now, user_agent),
        )
    return token


def get_session(token: str | None) -> dict | None:
    if not token:
        return None
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT s.token, s.user_id, u.username, s.created_at, s.last_seen
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND u.is_active = 1""",
            (token,),
        ).fetchone()
        if not row:
            return None
        # Expire sessions older than 30 days
        created = datetime.fromisoformat(row["created_at"]).replace(tzinfo=timezone.utc)
        if now - created > timedelta(days=30):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE token = ?", (now_iso, token)
        )
    return {"user_id": row["user_id"], "username": row["username"]}


def delete_session(token: str | None) -> None:
    if not token:
        return
    with _get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def list_users() -> list[dict]:
    """Return all users with session count and latest last_seen."""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT u.id, u.username, u.created_at, u.is_active,
                   COUNT(s.token)  AS session_count,
                   MAX(s.last_seen) AS last_seen
            FROM users u
            LEFT JOIN sessions s ON s.user_id = u.id
            GROUP BY u.id
            ORDER BY u.created_at ASC
        """).fetchall()
    return [dict(r) for r in rows]


def set_user_active(user_id: str, is_active: bool) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, user_id),
        )


def revoke_sessions(user_id: str) -> None:
    """Delete all sessions for a user (forces re-login on next request)."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def claim_legacy_data(user_id: str) -> None:
    """Assign all rows with user_id='' in cache.db to this user."""
    from .cache import get_conn as _cache_conn
    with _cache_conn() as conn:
        conn.execute("UPDATE holdings SET user_id = ? WHERE user_id = ''", (user_id,))
        conn.execute("UPDATE trades SET user_id = ? WHERE user_id = ''", (user_id,))
        conn.execute("UPDATE settings SET user_id = ? WHERE user_id = ''", (user_id,))
        conn.execute("UPDATE chat_messages SET user_id = ? WHERE user_id = ''", (user_id,))
