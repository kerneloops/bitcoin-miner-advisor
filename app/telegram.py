import os
from datetime import date

import httpx


async def send_message(text: str) -> tuple[bool, str]:
    """Returns (success, error_description)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )
        if r.status_code == 200:
            return True, ""
        try:
            body = r.json()
            return False, body.get("description", str(r.status_code))
        except Exception:
            return False, f"HTTP {r.status_code}"


async def notify_signals(tickers_data: dict):
    """Send alert for any BUY/SELL signals. tickers_data is the routes response dict."""
    alerts = [
        (t, d)
        for t, d in tickers_data.items()
        if d.get("recommendation") in ("BUY", "SELL")
    ]
    if not alerts:
        return

    lines = [f"<b>🚨 LAPIO ALERT — {date.today()}</b>\n"]
    for ticker, d in alerts:
        rec = d["recommendation"]
        emoji = "🟢" if rec == "BUY" else "🔴"
        g = d.get("position_guidance") or {}
        line = f"{emoji} <b>{ticker}</b> → {rec} [{d.get('confidence', '')}] @ ${d['current_price']:.2f}"
        if g.get("shares", 0) > 0:
            line += f"\n   ↳ {g['action']} {g['shares']} shares (~${g['amount']:.0f})"
        elif g.get("note"):
            line += f"\n   ↳ {g['note']}"
        if d.get("reasoning"):
            line += f"\n   {d['reasoning'][:120]}…"
        lines.append(line)
    lines.append("\nlapio.dev")
    await send_message("\n".join(lines))  # ignore result for bulk alerts
