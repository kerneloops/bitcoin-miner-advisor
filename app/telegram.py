import logging
import os
from datetime import date

import httpx
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)
_claude = AsyncAnthropic()

BOT_SYSTEM = """You are LAPIO, a sharp AI trading assistant specialising in:
- Bitcoin miner stocks and ETFs (WGMI, MARA, RIOT, BITX, RIOX, CIFU, BMNU, MSTX)
- Broader crypto (BTC, ETH, altcoins, on-chain signals, DeFi)
- AI and technology stocks (NVDA, AMD, MSFT, GOOG, META, TSLA, etc.)
- Macro and finance (rates, Fed, equities, commodities, risk-on/off regimes)

You have been given the user's current portfolio, live technical signals, and macro conditions.
Answer concisely and specifically. This is a personal decision-support tool — skip disclaimers.
Use plain text only — no markdown, no asterisks. Telegram HTML tags (<b>, <i>) are fine sparingly."""


async def _build_context() -> str:
    from . import cache
    from .data import TICKERS
    from .technicals import add_relative_strength, compute_signals

    lines = []

    holdings = cache.get_holdings()
    if holdings:
        lines.append("<b>Portfolio</b>")
        for h in holdings:
            lines.append(f"  {h['ticker']}: {h['shares']} shares @ ${h['avg_cost']:.2f}")
        lines.append(f"  Cash: ${cache.get_cash():.2f}")

    try:
        signals = add_relative_strength({t: compute_signals(t) for t in TICKERS})
        lines.append("\n<b>Current signals</b>")
        for ticker, s in signals.items():
            if "error" in s:
                continue
            history = cache.get_analysis_history(ticker, limit=1)
            rec = history[0]["recommendation"] if history else "—"
            price = s.get("current_price") or "—"
            rsi = s.get("rsi") or "—"
            lines.append(f"  {ticker}: ${price}  RSI {rsi}  Last rec: {rec}")
    except Exception:
        pass

    macro = cache.get_latest_macro()
    if macro:
        lines.append("\n<b>Macro</b>")
        if "fear_greed_value" in macro:
            lines.append(f"  Fear & Greed: {macro['fear_greed_value']}/100 ({macro.get('fear_greed_label', '')})")
        if "btc_dvol" in macro:
            lines.append(f"  BTC DVOL: {macro['btc_dvol']}")
        if "btc_funding_rate_pct" in macro:
            lines.append(f"  Funding rate: {macro['btc_funding_rate_pct']:+.4f}%")

    bias = cache.get_setting("macro_bias")
    if bias:
        lines.append(f"\n{bias}")

    return "\n".join(lines) if lines else "No cached data available yet — run an analysis first."


async def generate_reply(user_text: str) -> str:
    """Generate a Claude reply for the given user text. Stores both messages in chat history."""
    from . import cache
    cache.add_chat_message("user", user_text)
    context = await _build_context()
    try:
        resp = await _claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=BOT_SYSTEM + f"\n\nCurrent data:\n{context}",
            messages=[{"role": "user", "content": user_text}],
        )
        reply = resp.content[0].text.strip()
    except Exception as e:
        reply = f"Sorry, I hit an error: {e}"
    cache.add_chat_message("assistant", reply)
    return reply


async def handle_update(update: dict):
    """Process an incoming Telegram update and reply via Claude."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = str(message.get("chat", {}).get("id", ""))
    if chat_id != os.getenv("TELEGRAM_CHAT_ID", ""):
        return  # ignore messages from unknown chats

    text = (message.get("text") or "").strip()
    if not text:
        return

    if text in ("/start", "/help"):
        await send_message(
            "<b>LAPIO Bot</b>\n\n"
            "Ask me anything about your miner positions, signals, or market conditions.\n\n"
            "Examples:\n"
            "• Should I add to WGMI?\n"
            "• How is the macro looking?\n"
            "• What's my portfolio value?\n"
            "• Summarise today's signals"
        )
        return

    reply = await generate_reply(text)
    await send_message(reply)


async def setup_webhook():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
    if not token or not base_url:
        return
    secret = os.getenv("SESSION_SECRET", "")[:64]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={
                "url": f"{base_url}/api/telegram/webhook",
                "secret_token": secret,
                "allowed_updates": ["message"],
            },
        )
        if r.status_code == 200 and r.json().get("ok"):
            logger.info("Telegram webhook registered at %s/api/telegram/webhook", base_url)
        else:
            logger.warning("Telegram webhook setup failed: %s", r.text)


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
    from . import cache, push
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
    alert_text = "\n".join(lines)

    # Store in chat history so the iOS app shows it
    cache.add_chat_message("assistant", alert_text)

    await send_message(alert_text)  # ignore result for bulk alerts

    # Send push notifications to registered iOS devices
    if push.is_configured():
        tokens_json = cache.get_setting("push_device_tokens")
        if tokens_json:
            import json
            try:
                tokens = json.loads(tokens_json)
            except Exception:
                tokens = []
            push_title = f"LAPIO Alert — {date.today()}"
            push_body = ", ".join(
                f"{t} {d['recommendation']}" for t, d in alerts
            )
            for token in tokens:
                try:
                    await push.send_push(token, push_title, push_body)
                except Exception as e:
                    logger.warning(f"Push notification failed for token ...{token[-6:]}: {e}")
