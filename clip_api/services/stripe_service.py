import logging
import time
from typing import Optional

import stripe

from ..config import (
    STRIPE_PRICE_ENTERPRISE,
    STRIPE_PRICE_PRO,
    STRIPE_PRICE_STARTER,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    VALID_TIERS,
)

logger = logging.getLogger(__name__)

# Initialize Stripe
if not STRIPE_SECRET_KEY:
    logger.warning("STRIPE_SECRET_KEY not set - Stripe operations will fail")

stripe.api_key = STRIPE_SECRET_KEY

# Map tiers to Stripe price IDs
PRICE_IDS = {
    "developer": STRIPE_PRICE_STARTER,
    "professional": STRIPE_PRICE_PRO,
    "enterprise": STRIPE_PRICE_ENTERPRISE,
}


def _validate_stripe_config() -> None:
    """Validate Stripe configuration is complete."""
    if not STRIPE_SECRET_KEY:
        raise ValueError("STRIPE_SECRET_KEY not configured")

    missing_prices = [tier for tier in VALID_TIERS if not PRICE_IDS.get(tier)]
    if missing_prices:
        raise ValueError(f"Missing Stripe price IDs for tiers: {', '.join(missing_prices)}")


def _log_latency(event: str, elapsed_ms: float) -> None:
    if elapsed_ms >= 1000:
        logger.warning("Slow Stripe operation", extra={"event": event, "context": {"elapsed_ms": round(elapsed_ms, 2)}})


def create_checkout_session(
    tier: str,
    success_url: str,
    cancel_url: str,
    customer_email: Optional[str] = None,
) -> stripe.checkout.Session:
    """Create a Stripe checkout session for subscription signup."""
    if not tier or (tier.lower() not in VALID_TIERS):
        raise ValueError(f"Invalid tier: {tier}")

    _validate_stripe_config()

    normalized_tier = tier.lower()
    price_id = PRICE_IDS.get(normalized_tier)
    if not price_id:
        raise ValueError(f"Unknown tier: {normalized_tier}")

    start = time.perf_counter()
    try:
        params = {
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": cancel_url,
            "metadata": {"tier": normalized_tier},
        }
        if customer_email:
            params["customer_email"] = customer_email

        session = stripe.checkout.Session.create(**params)
        logger.info(
            "Checkout session created",
            extra={"event": "billing.checkout.created", "context": {"tier": normalized_tier, "session_id": session.id}},
        )
        return session
    except stripe.error.StripeError as exc:
        logger.exception("Stripe checkout failed: %s", exc)
        raise
    finally:
        _log_latency("billing.checkout.latency", (time.perf_counter() - start) * 1000)


def create_billing_portal_session(stripe_customer_id: str, return_url: str) -> stripe.billing_portal.Session:
    """Create a Stripe billing portal session for managing subscriptions."""
    if not stripe_customer_id:
        raise ValueError("stripe_customer_id is required")

    _validate_stripe_config()

    start = time.perf_counter()
    try:
        session = stripe.billing_portal.Session.create(customer=stripe_customer_id, return_url=return_url)
        logger.info(
            "Billing portal session created",
            extra={"event": "billing.portal.created", "context": {"session_id": session.id}},
        )
        return session
    except stripe.error.StripeError as exc:
        logger.exception("Stripe billing portal failed: %s", exc)
        raise
    finally:
        _log_latency("billing.portal.latency", (time.perf_counter() - start) * 1000)


def construct_webhook_event(payload: bytes, sig_header: str, webhook_secret: str) -> dict:
    """Construct and verify a Stripe webhook event."""
    if not webhook_secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")
    if not sig_header:
        raise ValueError("Missing Stripe signature header")

    start = time.perf_counter()
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        logger.info("Webhook event verified", extra={"event": "billing.webhook.verified", "context": {"event_type": event.get("type")}})
        return event
    except stripe.error.SignatureVerificationError as exc:
        logger.warning("Stripe webhook signature verification failed", extra={"event": "billing.webhook.signature_failed", "context": {"error": str(exc)}})
        raise
    except stripe.error.StripeError as exc:
        logger.exception("Stripe webhook processing failed: %s", exc)
        raise
    finally:
        _log_latency("billing.webhook.latency", (time.perf_counter() - start) * 1000)

