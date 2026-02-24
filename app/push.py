"""APNs push notification sender using JWT auth (no cert required)."""
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_APNS_HOST = "https://api.push.apple.com"


def is_configured() -> bool:
    return all(
        os.getenv(k)
        for k in ("APNS_TEAM_ID", "APNS_KEY_ID", "APNS_KEY_FILE", "APNS_BUNDLE_ID")
    )


def _make_jwt() -> str:
    import jwt  # PyJWT

    team_id = os.environ["APNS_TEAM_ID"]
    key_id = os.environ["APNS_KEY_ID"]
    key_file = os.environ["APNS_KEY_FILE"]

    with open(key_file, "r") as f:
        private_key = f.read()

    now = int(time.time())
    token = jwt.encode(
        {"iss": team_id, "iat": now},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )
    return token


async def send_push(device_token: str, title: str, body: str) -> None:
    bundle_id = os.environ["APNS_BUNDLE_ID"]
    jwt_token = _make_jwt()

    headers = {
        "authorization": f"bearer {jwt_token}",
        "apns-topic": bundle_id,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    payload = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
        }
    }

    url = f"{_APNS_HOST}/3/device/{device_token}"
    async with httpx.AsyncClient(http2=True, timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"APNs returned {r.status_code}: {r.text}")
        logger.info("Push sent to ...%s", device_token[-6:])
