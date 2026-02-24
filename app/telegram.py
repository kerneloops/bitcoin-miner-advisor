import json
import logging
import os
import time
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
When the user asks about any cryptocurrency price or performance, use the get_crypto_price tool.
Answer concisely and specifically. This is a personal decision-support tool — skip disclaimers.
Use plain text only — no markdown, no asterisks. Telegram HTML tags (<b>, <i>) are fine sparingly."""

_TOOLS = [
    {
        "name": "get_crypto_price",
        "description": (
            "Fetch the live price and % change (24h, 7d, 30d) for any cryptocurrency or token. "
            "Use this whenever the user asks about a coin's current price, performance, or market cap. "
            "Works for any coin — BTC, ETH, SOL, PEPE, any altcoin or token."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Coin name or ticker symbol, e.g. 'ETH', 'solana', 'PEPE', 'chainlink', 'dogecoin'",
                }
            },
            "required": ["query"],
        },
    }
]

# Simple in-memory price cache: query -> (json_result, timestamp)
_price_cache: dict[str, tuple[str, float]] = {}
_PRICE_CACHE_TTL = 60  # seconds


async def _fetch_crypto_price(query: str) -> str:
    """Search CoinGecko for any coin and return live price data as a JSON string."""
    key = query.lower().strip()
    if key in _price_cache:
        result, ts = _price_cache[key]
        if time.time() - ts < _PRICE_CACHE_TTL:
            return result

    async with httpx.AsyncClient(timeout=12) as client:
        # Step 1 — find the CoinGecko coin ID
        try:
            sr = await client.get(
                "https://api.coingecko.com/api/v3/search",
                params={"query": query},
            )
            sr.raise_for_status()
            coins = sr.json().get("coins", [])
        except Exception as e:
            return json.dumps({"error": f"Search failed: {e}"})

        if not coins:
            return json.dumps({"error": f"No cryptocurrency found matching '{query}'"})

        coin_id = coins[0]["id"]

        # Step 2 — fetch full market data
        try:
            pr = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "false",
                    "developer_data": "false",
                },
            )
            pr.raise_for_status()
            data = pr.json()
            md = data["market_data"]
        except Exception as e:
            return json.dumps({"error": f"Price fetch failed: {e}"})

    result = json.dumps({
        "name": data["name"],
        "symbol": data["symbol"].upper(),
        "price_usd": md["current_price"].get("usd"),
        "price_eur": md["current_price"].get("eur"),
        "change_24h_pct": md.get("price_change_percentage_24h"),
        "change_7d_pct": md.get("price_change_percentage_7d"),
        "change_30d_pct": md.get("price_change_percentage_30d"),
        "market_cap_usd": md["market_cap"].get("usd"),
        "market_cap_rank": data.get("market_cap_rank"),
    })
    _price_cache[key] = (result, time.time())
    return result


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


async def generate_reply(user_text: str) -> tuple[str, int]:
    """Generate a Claude reply, with tool use for live crypto prices.
    Returns (reply_text, user_msg_id) — user_msg_id lets the frontend skip
    the DB-stored user message it already rendered as an optimistic bubble."""
    from . import cache
    user_msg_id = cache.add_chat_message("user", user_text)
    context = await _build_context()
    system = BOT_SYSTEM + f"\n\nCurrent data:\n{context}"
    messages = [{"role": "user", "content": user_text}]

    try:
        for _ in range(5):  # max 5 agentic iterations
            resp = await _claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                system=system,
                tools=_TOOLS,
                messages=messages,
            )

            if resp.stop_reason == "tool_use":
                # Append assistant turn (may contain both text and tool_use blocks)
                messages.append({"role": "assistant", "content": resp.content})

                # Execute all tool calls and collect results
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use" and block.name == "get_crypto_price":
                        result = await _fetch_crypto_price(block.input.get("query", ""))
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "user", "content": tool_results})
                continue

            # stop_reason == "end_turn"
            text_blocks = [b for b in resp.content if hasattr(b, "text")]
            reply = text_blocks[0].text.strip() if text_blocks else "No response."
            break
        else:
            reply = "Sorry, I hit a tool loop — please try again."

    except Exception as e:
        reply = f"Sorry, I hit an error: {e}"

    cache.add_chat_message("assistant", reply)
    return reply, user_msg_id


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

    reply, _ = await generate_reply(text)
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
