import json

from anthropic import AsyncAnthropic

from . import cache

client = AsyncAnthropic()

TRADING_STYLES = {
    "balanced": "",
    "momentum": (
        "TRADING STYLE EMPHASIS: The user follows a momentum strategy. "
        "Prioritize 1W/1M returns, trend direction, and relative strength vs sector. "
        "Strong recent momentum is the primary buy signal; fading momentum is the primary sell signal. "
        "RSI and moving averages are secondary confirmation."
    ),
    "mean_reversion": (
        "TRADING STYLE EMPHASIS: The user follows a mean-reversion strategy. "
        "RSI is the primary signal — extreme oversold readings are buy opportunities, "
        "extreme overbought readings are sell triggers. Take a contrarian approach: "
        "buy fear, sell greed. Trend and momentum signals are secondary."
    ),
    "trend_following": (
        "TRADING STYLE EMPHASIS: The user follows a trend-following strategy. "
        "The SMA20/SMA50 relationship is the primary signal. Price above both SMAs with "
        "SMA20 > SMA50 (golden cross) is bullish; price below both with SMA20 < SMA50 "
        "(death cross) is bearish. RSI and short-term momentum are secondary confirmation."
    ),
}


def _build_style_section(signal_prefs: dict | None) -> str:
    if not signal_prefs:
        return ""
    parts = []
    style = signal_prefs.get("trading_style", "balanced")
    style_text = TRADING_STYLES.get(style, "")
    if style_text:
        parts.append(style_text)
    ob = signal_prefs.get("rsi_overbought", 70)
    os_val = signal_prefs.get("rsi_oversold", 30)
    if ob != 70 or os_val != 30:
        parts.append(
            f"RSI THRESHOLDS: The user considers RSI > {ob} as overbought "
            f"and RSI < {os_val} as oversold (instead of the standard 70/30)."
        )
    return "\n".join(parts)


CRYPTO_SYSTEM_PROMPT = """You are a disciplined, data-driven investment advisor specializing in Bitcoin miner ETFs and stocks.
You analyze technical signals and provide clear, reasoned daily buy/sell/hold recommendations.
Be concise, specific, and honest about uncertainty. Never give financial advice disclaimers — the user understands this is a personal decision-support tool."""

TECH_SYSTEM_PROMPT = """You are a disciplined, data-driven investment advisor specializing in AI, semiconductor, and technology stocks.
You analyze technical signals and provide clear, reasoned daily buy/sell/hold recommendations.
Be concise, specific, and honest about uncertainty. Never give financial advice disclaimers — the user understands this is a personal decision-support tool."""

GENERIC_SYSTEM_PROMPT = """You are a disciplined, data-driven investment advisor.
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


async def get_recommendation(ticker: str, signals: dict, btc_trend: str, fundamentals: dict | None = None, macro: dict | None = None, ticker_category: str = "crypto", signal_prefs: dict | None = None) -> dict:
    if ticker_category == "tech":
        system = TECH_SYSTEM_PROMPT
        btc_line = f"BTC 7-day trend (macro context): {btc_trend}"
        sector_hint = "Consider the broader AI/tech sector momentum, rates environment, and any ticker-specific catalysts implied by the signals."
        fund_section = ""
    elif ticker_category == "generic":
        system = GENERIC_SYSTEM_PROMPT
        btc_line = f"BTC 7-day trend (macro context): {btc_trend}"
        sector_hint = "Consider the broader market environment, sector dynamics, and any ticker-specific catalysts implied by the signals."
        fund_section = ""
    else:  # crypto
        system = CRYPTO_SYSTEM_PROMPT
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
    style_section = _build_style_section(signal_prefs)

    prompt = f"""Analyze {ticker} for today's decision.

Technical signals:
{json.dumps(signals, indent=2)}

{btc_line}
{fund_section}{macro_section}
{sector_hint}
{style_section}

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


async def generate_macro_bias(macro: dict, results: dict) -> str:
    """One-sentence synthesis of macro environment vs ticker recommendations."""
    rec_counts = {}
    for d in results.values():
        r = d.get("recommendation")
        if r:
            rec_counts[r] = rec_counts.get(r, 0) + 1

    rec_summary = ", ".join(f"{k}: {v}" for k, v in sorted(rec_counts.items()))
    macro_section = _macro_summary(macro)

    context_hint = "The user holds a mixed portfolio (crypto miners, tech stocks, and others). Focus on the cross-asset macro picture — rates, risk appetite, dollar strength, crypto sentiment — and how it affects their positions."

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
        system=GENERIC_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


async def run_analysis(all_signals: dict, fundamentals: dict | None = None, macro: dict | None = None, signal_prefs: dict | None = None) -> dict:
    from datetime import date
    from .data import classify_ticker

    btc_trend = _btc_trend_summary()
    run_date = date.today().isoformat()
    results = {}

    for ticker, signals in all_signals.items():
        if "error" in signals:
            results[ticker] = signals
            continue

        category = classify_ticker(ticker)
        ticker_fundamentals = fundamentals if category == "crypto" else None
        rec = await get_recommendation(ticker, signals, btc_trend, ticker_fundamentals, macro, category, signal_prefs)
        cache.save_analysis(
            run_date, ticker, signals, rec["recommendation"], rec.get("reasoning", ""),
            confidence=rec.get("confidence"), key_risk=rec.get("key_risk"),
        )
        results[ticker] = {**signals, **rec, "btc_trend": btc_trend}

    if macro:
        try:
            holdings = cache.get_all_holdings()
            held_results = {t: d for t, d in results.items() if t in holdings} if holdings else results
            bias = await generate_macro_bias(macro, held_results)
            cache.set_setting("macro_bias", bias)
        except Exception:
            pass

    return results
