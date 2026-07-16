import stripe
import os
from typing import Optional

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

PRODUCT_IDS = {
        "developer":    os.getenv("STRIPE_PRODUCT_DEVELOPER"),
        "professional": os.getenv("STRIPE_PRODUCT_PROFESSIONAL"),
        "enterprise":   os.getenv("STRIPE_PRODUCT_ENTERPRISE"),
}

TIER_LIMITS = {
        "developer":    500_000,
        "professional": 2_500_000,
        "enterprise":   10_000_000,
}

def _get_price_id(tier: str) -> str:
        product_id = PRODUCT_IDS.get(tier)
        if not product_id:
                    raise ValueError(f"Unknown tier: {tier}")
                prices = stripe.Price.list(product=product_id, active=True, limit=1)
    if not prices.data:
                raise ValueError(f"No active price found for tier: {tier}")
            return prices.data[0].id

def create_checkout_session(tier: str, success_url: str, cancel_url: str,
                                                         customer_email: Optional[str] = None):
                                                                 price_id = _get_price_id(tier)
                                                                 params = {
                                                                     "payment_method_types": ["card"],
                                                                     "line_items": [{"price": price_id, "quantity": 1}],
                                                                     "mode": "subscription",
                                                                     "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
                                                                     "cancel_url": cancel_url,
                                                                     "metadata": {"tier": tier},
                                                                 }
                                                                 if customer_email:
                                                                             params["customer_email"] = customer_email
                                                                         return stripe.checkout.Session.create(**params)

def create_billing_portal_session(stripe_customer_id: str, return_url: str):
        return stripe.billing_portal.Session.create(
                    customer=stripe_customer_id,
                    return_url=return_url,
        )

def construct_webhook_event(payload: bytes, sig_header: str, webhook_secret: str):
        return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
