import stripe
import logging
from typing import Optional

from ..config import STRIPE_SECRET_KEY, STRIPE_PRICE_STARTER, STRIPE_PRICE_PRO, STRIPE_PRICE_ENTERPRISE

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


def _validate_stripe_config():
    """Validate Stripe configuration is complete."""
    if not STRIPE_SECRET_KEY:
        raise ValueError("STRIPE_SECRET_KEY not configured")
    
    missing_prices = [
        tier for tier, price_id in PRICE_IDS.items() if not price_id
    ]
    if missing_prices:
        raise ValueError(
            f"Missing Stripe price IDs for tiers: {', '.join(missing_prices)}"
        )


def create_checkout_session(
    tier: str,
    success_url: str,
    cancel_url: str,
    customer_email: Optional[str] = None,
) -> stripe.checkout.Session:
    """Create a Stripe checkout session for subscription signup.
    
    Args:
        tier: Subscription tier ('developer', 'professional', 'enterprise')
        success_url: URL to redirect to after successful checkout
        cancel_url: URL to redirect to after cancelled checkout
        customer_email: Pre-fill customer email (optional)
        
    Returns:
        Stripe checkout session object with url and id
        
    Raises:
        ValueError: If tier is invalid or Stripe is not configured
    """
    _validate_stripe_config()
    
    price_id = PRICE_IDS.get(tier)
    if not price_id:
        raise ValueError(f"Unknown tier: {tier}")
    
    try:
        params = {
            "payment_method_types": ["card"],
            "line_items": [
                {
                    "price": price_id,
                    "quantity": 1,
                }
            ],
            "mode": "subscription",
            "success_url": f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": cancel_url,
            "metadata": {"tier": tier},
        }
        if customer_email:
            params["customer_email"] = customer_email
        
        session = stripe.checkout.Session.create(**params)
        logger.info(
            f"Checkout session created: {session.id} for tier={tier} "
            f"email={customer_email or 'unknown'}"
        )
        return session
    except stripe.error.StripeError as e:
        logger.error(f"Stripe checkout error: {e}", exc_info=True)
        raise


def create_billing_portal_session(
    stripe_customer_id: str,
    return_url: str,
) -> stripe.billing_portal.Session:
    """Create a Stripe billing portal session for managing subscriptions.
    
    Args:
        stripe_customer_id: Stripe customer ID
        return_url: URL to return to after leaving billing portal
        
    Returns:
        Stripe billing portal session object with url
        
    Raises:
        ValueError: If Stripe is not configured
    """
    _validate_stripe_config()
    
    try:
        session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=return_url,
        )
        logger.info(f"Billing portal session created for customer: {stripe_customer_id}")
        return session
    except stripe.error.StripeError as e:
        logger.error(f"Stripe billing portal error: {e}", exc_info=True)
        raise


def construct_webhook_event(
    payload: bytes,
    sig_header: str,
    webhook_secret: str,
) -> dict:
    """Construct and verify a Stripe webhook event.
    
    Args:
        payload: Raw request body from Stripe
        sig_header: Stripe signature header value
        webhook_secret: Webhook endpoint secret
        
    Returns:
        Verified webhook event dictionary
        
    Raises:
        stripe.error.SignatureVerificationError: If signature is invalid
    """
    if not webhook_secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        logger.info(f"Webhook event verified: {event['type']}")
        return event
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Stripe webhook signature verification failed: {e}")
        raise

