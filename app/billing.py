"""Stripe billing integration — checkout, portal, webhook handling."""

import logging
import os

import stripe

logger = logging.getLogger(__name__)

# ── Stripe config ─────────────────────────────────────────────────────────────

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

# Map Stripe Price IDs → internal tier names
PRICE_TO_TIER: dict[str, str] = {}

def _load_price_map():
    """Populate PRICE_TO_TIER from env vars (called at import time)."""
    mapping = {
        "STRIPE_PRICE_HASHRATE_MONTHLY": "hashrate",
        "STRIPE_PRICE_HASHRATE_ANNUAL": "hashrate",
        "STRIPE_PRICE_BLOCKRATE_MONTHLY": "blockrate",
        "STRIPE_PRICE_BLOCKRATE_ANNUAL": "blockrate",
        "STRIPE_PRICE_DIFFICULTY_MONTHLY": "difficulty",
        "STRIPE_PRICE_DIFFICULTY_ANNUAL": "difficulty",
    }
    for env_key, tier in mapping.items():
        price_id = os.getenv(env_key, "")
        if price_id:
            PRICE_TO_TIER[price_id] = tier

_load_price_map()

# ── Tier definitions ──────────────────────────────────────────────────────────

TIER_LEVELS = {
    "expired": -1,
    "hashrate": 1,
    "blockrate": 2,
    "difficulty": 3,
    "admin": 99,
}

TIER_LIMITS = {
    "trial": {"max_tickers": 15, "chat_daily": 20, "tier_level": 2},
    "hashrate": {"max_tickers": 10, "chat_daily": 0, "tier_level": 1},
    "blockrate": {"max_tickers": 15, "chat_daily": 20, "tier_level": 2},
    "difficulty": {"max_tickers": 999, "chat_daily": 999, "tier_level": 3},
    "admin": {"max_tickers": 999, "chat_daily": 999, "tier_level": 99},
    "expired": {"max_tickers": 0, "chat_daily": 0, "tier_level": -1},
}

HASHRATE_PRESET_TICKERS = [
    "WGMI", "MARA", "RIOT", "BITX", "MSTX",
    "NVDA", "AMD", "MSFT", "GOOGL", "META",
]

TIER_PRICES = {
    "hashrate": {"monthly": 9, "annual": 84},
    "blockrate": {"monthly": 19, "annual": 180},
    "difficulty": {"monthly": 39, "annual": 348},
}


def get_tier_limits(tier: str) -> dict:
    """Return limits dict for the given tier."""
    return TIER_LIMITS.get(tier, TIER_LIMITS["expired"])


def get_tier_level(tier: str) -> int:
    """Return numeric level for tier comparison."""
    return TIER_LEVELS.get(tier, -1)


def require_tier(user_tier: str, min_level: int, feature_name: str):
    """Raise 403 if user's tier is below min_level."""
    from fastapi import HTTPException
    level = get_tier_level(user_tier)
    if level < min_level:
        raise HTTPException(
            403,
            detail={
                "code": "upgrade_required",
                "feature": feature_name,
                "current_tier": user_tier,
                "required_level": min_level,
            },
        )


# ── Stripe Checkout ──────────────────────────────────────────────────────────

def create_checkout_session(
    user_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout session and return the URL."""
    from . import users as user_store

    user = user_store.get_user_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            metadata={"user_id": user_id, "username": user.get("username", "")},
        )
        customer_id = customer.id
        user_store.update_subscription_fields(user_id, stripe_customer_id=customer_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        subscription_data={"trial_period_days": None},
        metadata={"user_id": user_id},
    )
    return session.url


# ── Stripe Customer Portal ───────────────────────────────────────────────────

def create_portal_session(user_id: str, return_url: str) -> str:
    """Create a Stripe Customer Portal session and return the URL."""
    from . import users as user_store

    user = user_store.get_user_by_id(user_id)
    if not user or not user.get("stripe_customer_id"):
        raise ValueError("No Stripe customer for this user")

    session = stripe.billing_portal.Session.create(
        customer=user["stripe_customer_id"],
        return_url=return_url,
    )
    return session.url


# ── Webhook handler ──────────────────────────────────────────────────────────

def handle_webhook_event(payload: bytes, sig_header: str) -> dict:
    """Process a Stripe webhook event. Returns {"ok": True} or raises."""
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")

    event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    event_type = event["type"]
    data_obj = event["data"]["object"]

    from . import users as user_store

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data_obj, user_store)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(data_obj, user_store)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data_obj, user_store)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data_obj, user_store)
    else:
        logger.debug(f"Unhandled Stripe event: {event_type}")

    return {"ok": True}


def _handle_checkout_completed(session, user_store):
    """User completed checkout — activate their subscription."""
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    if not customer_id or not subscription_id:
        return

    user = user_store.get_user_by_stripe_customer(customer_id)
    if not user:
        # Try metadata fallback
        uid = (session.get("metadata") or {}).get("user_id")
        if uid:
            user_store.update_subscription_fields(uid, stripe_customer_id=customer_id)
            user = user_store.get_user_by_id(uid)
    if not user:
        logger.warning(f"checkout.session.completed: no user for customer {customer_id}")
        return

    # Look up the subscription to get the price → tier
    sub = stripe.Subscription.retrieve(subscription_id)
    price_id = sub["items"]["data"][0]["price"]["id"] if sub["items"]["data"] else None
    tier = PRICE_TO_TIER.get(price_id, "hashrate")

    user_store.update_subscription_fields(
        user["id"],
        stripe_subscription_id=subscription_id,
        subscription_tier=tier,
        subscription_status="active",
    )
    logger.info(f"Subscription activated: user={user['id']} tier={tier}")


def _handle_subscription_updated(sub, user_store):
    """Subscription changed (upgrade, downgrade, cancel scheduled)."""
    customer_id = sub.get("customer")
    user = user_store.get_user_by_stripe_customer(customer_id)
    if not user:
        return

    price_id = sub["items"]["data"][0]["price"]["id"] if sub["items"]["data"] else None
    tier = PRICE_TO_TIER.get(price_id, user.get("subscription_tier", "hashrate"))
    status = sub.get("status", "active")  # active, past_due, canceled, etc.

    updates = {
        "subscription_tier": tier,
        "subscription_status": status,
        "stripe_subscription_id": sub.get("id"),
    }

    # If canceling at period end, record the end date
    if sub.get("cancel_at_period_end"):
        from datetime import datetime, timezone
        period_end = sub.get("current_period_end")
        if period_end:
            end_dt = datetime.fromtimestamp(period_end, tz=timezone.utc)
            updates["subscription_ends_at"] = end_dt.isoformat(timespec="seconds")
    else:
        updates["subscription_ends_at"] = None

    user_store.update_subscription_fields(user["id"], **updates)
    logger.info(f"Subscription updated: user={user['id']} tier={tier} status={status}")


def _handle_subscription_deleted(sub, user_store):
    """Subscription fully canceled/expired."""
    customer_id = sub.get("customer")
    user = user_store.get_user_by_stripe_customer(customer_id)
    if not user:
        return

    user_store.update_subscription_fields(
        user["id"],
        subscription_tier="expired",
        subscription_status="expired",
    )
    logger.info(f"Subscription expired: user={user['id']}")


def _handle_payment_failed(invoice, user_store):
    """Payment failed — mark subscription as past_due."""
    customer_id = invoice.get("customer")
    user = user_store.get_user_by_stripe_customer(customer_id)
    if not user:
        return

    user_store.update_subscription_fields(
        user["id"],
        subscription_status="past_due",
    )
    logger.info(f"Payment failed: user={user['id']}")
