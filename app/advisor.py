import json

from anthropic import AsyncAnthropic

from . import cache

client = AsyncAnthropic()

SYSTEM_PROMPT = """You are a disciplined, data-driven investment advisor specializing in Bitcoin miner ETFs and stocks.
You analyze technical signals and provide clear, reasoned weekly buy/sell/hold recommendations.
Be concise, specific, and honest about uncertainty. Never give financial advice disclaimers — the user understands this is a personal decision-support tool."""


def _btc_trend_summary() -> str:
    btc_rows = cache.get_prices("BTC", limit=10)
    if len(btc_rows) < 7:
        return "unavailable"
    week_ago = float(btc_rows[-7]["close"])
    now = float(btc_rows[-1]["close"])
    pct = round((now / week_ago - 1) * 100, 2)
    return f"{pct:+.1f}% over 7 days (current: ${now:,.0f})"


async def get_recommendation(ticker: str, signals: dict, btc_trend: str, fundamentals: dict | None = None) -> dict:
    fund_section = ""
    if fundamentals:
        fund_section = f"""
Bitcoin network fundamentals:
- Hashprice: ${fundamentals.get('hashprice_usd_per_ph_day', 'N/A')}/PH/day (excludes tx fees)
- Network hashrate: {fundamentals.get('network_hashrate_eh', 'N/A')} EH/s
- Next difficulty adjustment: {fundamentals.get('difficulty_change_pct', 'N/A'):+.2f}% in {fundamentals.get('days_until_retarget', 'N/A')} days ({fundamentals.get('difficulty_progress_pct', 'N/A')}% through epoch)
- Previous retarget: {fundamentals.get('previous_retarget_pct', 'N/A'):+.2f}%
- Avg block time: {fundamentals.get('block_time_min', 'N/A')} min (target: 10 min)
"""

    prompt = f"""Analyze {ticker} for this week's decision.

Technical signals:
{json.dumps(signals, indent=2)}

BTC 7-day trend: {btc_trend}
{fund_section}
Consider how hashprice trend and the upcoming difficulty adjustment affect miner profitability and sector sentiment.

Respond ONLY with valid JSON (no markdown):
{{"recommendation": "BUY|SELL|HOLD", "confidence": "LOW|MEDIUM|HIGH", "reasoning": "2-3 sentences", "key_risk": "one sentence"}}"""

    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    # Strip markdown code fences if present
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    return json.loads(text.strip())


async def run_analysis(all_signals: dict, fundamentals: dict | None = None) -> dict:
    from datetime import date

    btc_trend = _btc_trend_summary()
    run_date = date.today().isoformat()
    results = {}

    for ticker, signals in all_signals.items():
        if "error" in signals:
            results[ticker] = signals
            continue

        rec = await get_recommendation(ticker, signals, btc_trend, fundamentals)
        cache.save_analysis(
            run_date, ticker, signals, rec["recommendation"], rec.get("reasoning", "")
        )
        results[ticker] = {**signals, **rec, "btc_trend": btc_trend}

    return results
