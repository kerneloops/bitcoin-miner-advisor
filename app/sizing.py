TIERS = {
    "conservative": {
        "buy_deploy_pct": 0.03,
        "max_position_pct": 0.05,
        "sell_pct": 0.50,
        "min_confidence": "HIGH",
    },
    "neutral": {
        "buy_deploy_pct": 0.06,
        "max_position_pct": 0.10,
        "sell_pct": 0.75,
        "min_confidence": "MEDIUM",
    },
    "aggressive": {
        "buy_deploy_pct": 0.12,
        "max_position_pct": 0.20,
        "sell_pct": 1.00,
        "min_confidence": "LOW",
    },
}

CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def compute_guidance(ticker, rec, confidence, price, shares_held, tier_name, total_capital):
    """Returns dict: {action, shares, amount, pct_of_capital, note} or None for HOLD."""
    if rec == "HOLD" or not total_capital or not price:
        return None
    tier = TIERS.get(tier_name, TIERS["neutral"])
    conf_ok = CONFIDENCE_RANK.get(confidence, 0) >= CONFIDENCE_RANK[tier["min_confidence"]]

    if rec == "BUY":
        if not conf_ok:
            return {
                "action": "BUY",
                "shares": 0,
                "note": f"Confidence {confidence} below {tier['min_confidence']} threshold for {tier_name}",
            }
        deploy = total_capital * tier["buy_deploy_pct"]
        current_val = (shares_held or 0) * price
        max_val = total_capital * tier["max_position_pct"]
        available = max(0, max_val - current_val)
        deploy = min(deploy, available)
        if deploy < price:
            return {"action": "BUY", "shares": 0, "note": "Already at max position for tier"}
        shares = int(deploy / price)
        amount = shares * price
        return {
            "action": "BUY",
            "shares": shares,
            "amount": amount,
            "pct_of_capital": round(amount / total_capital * 100, 2),
        }

    if rec == "SELL":
        if not shares_held or shares_held <= 0:
            return {"action": "SELL", "shares": 0, "note": "No position held"}
        shares = max(1, int(shares_held * tier["sell_pct"]))
        amount = shares * price
        return {
            "action": "SELL",
            "shares": shares,
            "amount": amount,
            "pct_of_holding": round(tier["sell_pct"] * 100, 0),
        }
    return None
