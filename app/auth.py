import hmac
import hashlib
import os


def _secret() -> bytes:
    return os.getenv("SESSION_SECRET", "change-me-in-env").encode()


def make_token() -> str:
    return hmac.new(_secret(), b"authenticated", hashlib.sha256).hexdigest()


def verify_token(token: str | None) -> bool:
    if not token:
        return False
    expected = hmac.new(_secret(), b"authenticated", hashlib.sha256).hexdigest()
    return hmac.compare_digest(token, expected)
