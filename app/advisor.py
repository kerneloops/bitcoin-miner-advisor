import json

from anthropic import AsyncAnthropic

from . import cache

client = AsyncAnthropic()

SYSTEM_PROMPT = """You are a disciplined, data-driven investment advisor specializing in Bitcoin miner ETFs and stocks.
You analyze technical signals and provide clear, reasoned daily buy/sell/hold recommendations.
Be concise, specific, and honest about uncertainty. Never give financial advice disclaimers — the user understands this is a personal decision-support tool."""

TECH_SYSTEM_PROMPT = """You are a disciplined, data-driven investment advisor specializing in AI, semiconductor, and technology stocks.
You analyze technical signals and provide clear, reasoned daily buy/sell/hold recommendations.
Be concise, specific, and honest about uncertainty. Never give financial advice disclaimers — the user understands this is a personal decision-support tool."""


def _btc_trend_summary() -> str:
    btc_rows = cache.get_prices("BTC", limit=10)
    if len(btc_rows) < 7:
        return "unavailable"
    week_ago = float(btc_rows[-7]["close"])
    now = float(btc_rows[-1]["close"])
    pct = round((now / week_ago - 1) * 100, 2)
    return f"{pct:+.1f}% over 7 days (current: ${now:,.0f})"


def _macro_summary(macro: dict) -> str:
    if not macro:
        return ""
    lines = []
    if "btc_dvol" in macro:
        lvl = "elevated" if macro["btc_dvol"] > 60 else "normal"
        lines.append(f"- BTC 30d IV (DVOL): {macro['btc_dvol']} ({lvl})")
    if "btc_funding_rate_pct" in macro:
        r = macro["btc_funding_rate_pct"]
        sentiment = "crowded long" if r > 0.03 else "crowded short" if r < -0.01 else "neutral"
        lines.append(f"- BTC perp funding rate: {r:+.4f}% ({sentiment})")
    if "fear_greed_value" in macro:
        lines.append(f"- Crypto Fear & Greed: {macro['fear_greed_value']}/100 ({macro.get('fear_greed_label', '')})")
    if "puell_multiple" in macro:
        p = macro["puell_multiple"]
        ctx = "miner capitulation zone" if p < 0.5 else "miner euphoria" if p > 2.0 else "normal range"
        lines.append(f"- Puell Multiple: {p} ({ctx})")
    if "vix" in macro:
        lines.append(f"- VIX: {macro['vix']}")
    if "us_2y_yield" in macro:
        lines.append(f"- US 2Y Treasury yield: {macro['us_2y_yield']}%")
    if "dxy" in macro:
        lines.append(f"- US Dollar Index: {macro['dxy']}")
    if "hy_spread" in macro:
        lines.append(f"- HY credit spread: {macro['hy_spread']}%")
    return "\nMacro & market context:\n" + "\n".join(lines) if lines else ""


async def get_recommendation(ticker: str, signals: dict, btc_trend: str, fundamentals: dict | None = None, macro: dict | None = None, universe: str = "miners") -> dict:
    if universe == "tech":
        system = TECH_SYSTEM_PROMPT
        btc_line = f"BTC 7-day trend (macro context): {btc_trend}"
        sector_hint = "Consider the broader AI/tech sector momentum, rates environment, and any ticker-specific catalysts implied by the signals."
        fund_section = ""
    else:
        system = SYSTEM_PROMPT
        btc_line = f"BTC 7-day trend: {btc_trend}"
        sector_hint = "Consider how hashprice trend and the upcoming difficulty adjustment affect miner profitability and sector sentiment."
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

    macro_section = _macro_summary(macro or {})

    prompt = f"""Analyze {ticker} for today's decision.

Technical signals:
{json.dumps(signals, indent=2)}

{btc_line}
{fund_section}{macro_section}
{sector_hint}

Respond ONLY with valid JSON (no markdown):
{{"recommendation": "BUY|SELL|HOLD", "confidence": "LOW|MEDIUM|HIGH", "reasoning": "2-3 sentences", "key_risk": "one sentence"}}"""

    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    # Strip markdown code fences if present
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    return json.loads(text.strip())


async def generate_macro_bias(macro: dict, results: dict, universe: str = "miners") -> str:
    """One-sentence synthesis of macro environment vs ticker recommendations."""
    rec_counts = {}
    for d in results.values():
        r = d.get("recommendation")
        if r:
            rec_counts[r] = rec_counts.get(r, 0) + 1

    rec_summary = ", ".join(f"{k}: {v}" for k, v in sorted(rec_counts.items()))
    macro_section = _macro_summary(macro)

    if universe == "tech":
        context_hint = "Focus on implications for AI/semiconductor/tech equities (valuations, growth stocks, rate sensitivity, dollar impact on multinationals)."
        sys_prompt = TECH_SYSTEM_PROMPT
    else:
        context_hint = "Focus on implications for Bitcoin miners and crypto assets (BTC price sensitivity, risk-on/off, DXY headwinds)."
        sys_prompt = SYSTEM_PROMPT

    prompt = f"""Given these macro signals:
{macro_section}

And these recommendations for the user's held positions today: {rec_summary}

{context_hint}

Write exactly ONE sentence (max 30 words) for a "Macro environment" summary line.
Explain the overall macro picture and, if there's tension between macro sentiment and the held-position recommendations, name it directly.
Start with "Macro environment:" and be specific — no vague language.
Respond with only the sentence, no JSON, no markdown."""

    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        system=sys_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


async def run_analysis(all_signals: dict, fundamentals: dict | None = None, macro: dict | None = None, universe: str = "miners") -> dict:
    from datetime import date

    btc_trend = _btc_trend_summary()
    run_date = date.today().isoformat()
    results = {}

    for ticker, signals in all_signals.items():
        if "error" in signals:
            results[ticker] = signals
            continue

        rec = await get_recommendation(ticker, signals, btc_trend, fundamentals, macro, universe)
        cache.save_analysis(
            run_date, ticker, signals, rec["recommendation"], rec.get("reasoning", ""),
            confidence=rec.get("confidence"), key_risk=rec.get("key_risk"),
        )
        results[ticker] = {**signals, **rec, "btc_trend": btc_trend}

    if macro:
        try:
            holdings = cache.get_all_holdings()
            held_results = {t: d for t, d in results.items() if t in holdings} if holdings else results
            bias = await generate_macro_bias(macro, held_results, universe)
            bias_key = "macro_bias_tech" if universe == "tech" else "macro_bias"
            cache.set_setting(bias_key, bias)
        except Exception:
            pass

    return results
