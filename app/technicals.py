import pandas as pd

from . import cache


def compute_rsi(series: pd.Series, period: int = 14) -> float | None:
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def compute_signals(ticker: str) -> dict:
    rows = cache.get_prices(ticker, limit=100)
    if len(rows) < 20:
        return {"ticker": ticker, "error": "Insufficient data — run a refresh first."}

    df = pd.DataFrame(rows)
    close = df["close"].astype(float)
    current = float(close.iloc[-1])

    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
    rsi = compute_rsi(close)

    week_return = round((current / float(close.iloc[-6]) - 1) * 100, 2) if len(df) >= 6 else None
    month_return = round((current / float(close.iloc[-22]) - 1) * 100, 2) if len(df) >= 22 else None

    # 30-day rolling correlation with BTC
    btc_correlation = None
    btc_rows = cache.get_prices("BTC", limit=60)
    if len(btc_rows) >= 30 and len(rows) >= 30:
        btc_series = pd.DataFrame(btc_rows).set_index("date")["close"].astype(float)
        ticker_series = df.set_index("date")["close"].astype(float)
        merged = pd.concat([ticker_series, btc_series], axis=1, keys=["ticker", "btc"]).dropna()
        if len(merged) >= 10:
            corr = merged["ticker"].pct_change().corr(merged["btc"].pct_change())
            btc_correlation = round(float(corr), 3)

    return {
        "ticker": ticker,
        "current_price": round(current, 2),
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2) if sma50 is not None else None,
        "above_sma20": current > sma20,
        "above_sma50": (current > sma50) if sma50 is not None else None,
        "rsi": rsi,
        "week_return_pct": week_return,
        "month_return_pct": month_return,
        "btc_correlation": btc_correlation,
    }


def add_relative_strength(all_signals: dict) -> dict:
    """Add vs-sector delta columns to each ticker's signals dict."""
    week_vals = {t: s["week_return_pct"] for t, s in all_signals.items() if s.get("week_return_pct") is not None}
    month_vals = {t: s["month_return_pct"] for t, s in all_signals.items() if s.get("month_return_pct") is not None}

    avg_1w = sum(week_vals.values()) / len(week_vals) if week_vals else None
    avg_1m = sum(month_vals.values()) / len(month_vals) if month_vals else None

    for ticker, signals in all_signals.items():
        w = signals.get("week_return_pct")
        m = signals.get("month_return_pct")
        signals["vs_sector_1w"] = round(w - avg_1w, 2) if (w is not None and avg_1w is not None) else None
        signals["vs_sector_1m"] = round(m - avg_1m, 2) if (m is not None and avg_1m is not None) else None

    return all_signals
