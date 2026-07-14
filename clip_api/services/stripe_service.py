import os
import stripe
import logging

from .key_manager import key_manager

LOGGER = logging.getLogger("stripe-service")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_STARTER = os.environ.get("STRIPE_PRICE_STARTER", "")
STRIPE_PRICE_PRO = os.environ.get("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_ENTERPRISE = os.environ.get("STRIPE_PRICE_ENTERPRISE", "")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


class StripeService:
    def __init__(self):
        self.secret_key = STRIPE_SECRET_KEY
        self.webhook_secret = STRIPE_WEBHOOK_SECRET
        self.price_map = {
            STRIPE_PRICE_STARTER: "starter",
            STRIPE_PRICE_PRO: "pro",
            STRIPE_PRICE_ENTERPRISE: "enterprise",
        }

    def create_checkout(self, price_id: str, success_url: str, cancel_url: str):
        if not self.secret_key:
            return {"error": "Stripe not configured"}, 400
        valid_prices = [STRIPE_PRICE_STARTER, STRIPE_PRICE_PRO, STRIPE_PRICE_ENTERPRISE]
        if price_id not in valid_prices:
            return {"error": "Invalid price_id"}, 400
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price": price_id, "quantity": 1}],
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_creation="always",
                billing_address_collection="required",
            )
            return {"checkout_url": session.url, "session_id": session.id}, 201
        except stripe.error.StripeError as e:
            LOGGER.error(f"Stripe error: {e}")
            return {"error": "Failed to create checkout session"}, 500
        except Exception as e:
            LOGGER.error(f"Checkout error: {e}")
            return {"error": "Failed to create checkout session"}, 500

    def process_webhook(self, payload: bytes, sig_header: str):
        if not self.webhook_secret:
            return {"error": "Stripe webhook not configured"}, 400
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, self.webhook_secret)
        except ValueError:
            return {"error": "Invalid payload"}, 400
        except stripe.error.SignatureVerificationError:
            return {"error": "Invalid signature"}, 400

        event_type = event.get("type")
        data = event.get("data", {}).get("object", {})

        if event_type == "checkout.session.completed":
            customer_email = data.get("customer_details", {}).get("email")
            stripe_customer_id = data.get("customer")
            stripe_subscription_id = data.get("subscription")
            price_id = None
            if data.get("line_items"):
                price_id = data["line_items"]["data"][0]["price"]["id"]
            elif stripe_subscription_id:
                try:
                    sub = stripe.Subscription.retrieve(stripe_subscription_id)
                    price_id = sub["items"]["data"][0]["price"]["id"]
                except Exception as e:
                    LOGGER.error(f"Error retrieving subscription: {e}")
            tier = self.price_map.get(price_id, "free")
            if customer_email and tier != "free":
                try:
                    api_key = key_manager.create_api_key(
                        customer_email, tier,
                        stripe_cid=stripe_customer_id,
                        stripe_sid=stripe_subscription_id,
                    )
                    LOGGER.info(f"Upgraded {customer_email} to {tier} — key {api_key}")
                except Exception as e:
                    LOGGER.error(f"Error upgrading {customer_email}: {e}")

        elif event_type == "customer.subscription.deleted":
            stripe_customer_id = data.get("customer")
            if stripe_customer_id:
                with key_manager.db:
                    cur = key_manager.db.execute(
                        "SELECT email FROM users WHERE stripe_customer_id=?",
                        (stripe_customer_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        key_manager.db.execute(
                            "UPDATE users SET tier='free', stripe_subscription_id=NULL "
                            "WHERE stripe_customer_id=?",
                            (stripe_customer_id,),
                        )
                        LOGGER.info(f"Downgraded {row[0]} to free tier")
                    else:
                        LOGGER.warning(f"No user found for customer {stripe_customer_id}")

        return {"status": "success"}, 200


stripe_service = StripeService()
