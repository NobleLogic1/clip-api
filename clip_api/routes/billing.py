import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ..config import BASE_URL, STRIPE_WEBHOOK_SECRET, TIER_LIMITS, VALID_TIERS
from ..services.key_manager import (
    create_customer,
    create_free_key,
    get_usage_stats,
    update_customer_status,
    validate_api_key,
)
from ..services.stripe_service import (
    construct_webhook_event,
    create_billing_portal_session,
    create_checkout_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["billing"])


class CheckoutRequest(BaseModel):
    tier: str
    email: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={"examples": [{"tier": "professional", "email": "user@example.com"}]})


class FreeKeyRequest(BaseModel):
    email: str

    model_config = ConfigDict(json_schema_extra={"examples": [{"email": "developer@example.com"}]})


class PortalRequest(BaseModel):
    api_key: str

    model_config = ConfigDict(json_schema_extra={"examples": [{"api_key": "clip_..."}]})


def _client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP (Railway / proxies put real IP in X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


@router.post("/billing/free-key")
async def create_free_api_key(body: FreeKeyRequest, request: Request):
    """
    Create (or return) a Free-tier API key with only an email.
    No credit card / Stripe required.

    Anti-abuse:
    - One free key per email (always reuses existing key, even if exhausted)
    - Max 3 *new* free keys per IP per 24 hours
    """
    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    client_ip = _client_ip(request)

    try:
        customer_id, api_key, meta = create_free_key(email, client_ip=client_ip)

        message = (
            "Existing free key returned. Quota does not reset by requesting a new key."
            if meta.get("reused")
            else "Free key created. Perfect for prototyping. Upgrade when you are ready for production."
        )

        if meta.get("calls_remaining", 1) == 0:
            message = (
                "Your free quota for this month is exhausted. "
                "Upgrade to Developer ($29/mo) to continue. "
                "Creating a new free key will not increase your limit."
            )

        return {
            "api_key": api_key,
            "tier": "free",
            "monthly_limit": TIER_LIMITS["free"],
            "calls_used": meta.get("calls_used", 0),
            "calls_remaining": meta.get("calls_remaining", TIER_LIMITS["free"]),
            "reused": meta.get("reused", False),
            "email": email,
            "message": message,
        }
    except ValueError as exc:
        raise HTTPException(status_code=429 if "Too many free keys" in str(exc) else 400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Free key creation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create free API key") from exc


@router.post("/billing/checkout")
async def create_checkout(body: CheckoutRequest):
    """Create a Stripe checkout session for subscribing to a paid tier."""
    normalized_tier = (body.tier or "").lower()

    if normalized_tier == "free":
        raise HTTPException(
            status_code=400,
            detail="Free tier does not require payment. Use POST /billing/free-key with your email instead.",
        )

    if normalized_tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Choose: {', '.join(VALID_TIERS)}")

    try:
        session = create_checkout_session(
            tier=normalized_tier,
            success_url=f"{BASE_URL}/billing/success",
            cancel_url=f"{BASE_URL}/billing/cancel",
            customer_email=body.email,
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except ValueError as exc:
        logger.warning("Checkout validation error", extra={"event": "billing.checkout.validation_failed", "context": {"tier": normalized_tier}})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Checkout creation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create checkout session") from exc


@router.post("/billing/portal")
async def billing_portal(body: PortalRequest):
    """Get a Stripe billing portal URL for managing subscription."""
    customer = validate_api_key(body.api_key)
    if not customer:
        logger.warning("Portal access denied for invalid API key", extra={"event": "billing.portal.auth_failed", "context": {"reason": "invalid_key"}})
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    try:
        stripe_customer_id = customer.get("stripe_customer_id")
        if not stripe_customer_id or str(stripe_customer_id).startswith("free_"):
            raise HTTPException(status_code=400, detail="No Stripe customer associated with this API key (Free tier has no billing portal)")

        session = create_billing_portal_session(stripe_customer_id, BASE_URL)
        return {"portal_url": session.url}
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("Portal validation error", extra={"event": "billing.portal.validation_failed"})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Billing portal creation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create billing portal") from exc


@router.get("/billing/usage")
async def get_usage(authorization: str = Header(...)):
    """Get current API usage for the authenticated customer."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Use: Authorization: Bearer <api_key>")

    api_key = authorization[7:].strip()
    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    stats = get_usage_stats(api_key)
    if not stats:
        raise HTTPException(status_code=404, detail="Usage stats not found")

    return stats


@router.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook endpoint for subscription events."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    if not sig:
        logger.warning("Stripe webhook missing signature header")
        raise HTTPException(status_code=400, detail="Missing Stripe signature header")

    try:
        event = construct_webhook_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except ValueError as exc:
        logger.warning("Webhook validation error", extra={"event": "billing.webhook.validation_failed", "context": {"error": str(exc)}})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Webhook processing failed: %s", exc)
        raise HTTPException(status_code=400, detail="Webhook verification failed") from exc

    event_type = event.get("type")
    obj = event.get("data", {}).get("object", {})

    try:
        if event_type == "checkout.session.completed":
            stripe_customer_id = obj.get("customer")
            if not stripe_customer_id:
                logger.warning("Checkout session completed without Stripe customer ID", extra={"event": "billing.webhook.missing_customer", "context": {"event_type": event_type}})
                return JSONResponse({"status": "ignored", "reason": "missing_customer_id"})
            customer_id, api_key = create_customer(
                stripe_customer_id=stripe_customer_id,
                email=obj.get("customer_details", {}).get("email", "unknown"),
                tier=obj.get("metadata", {}).get("tier", "developer"),
                subscription_id=obj.get("subscription"),
            )
            logger.info(
                "New subscription",
                extra={"event": "billing.webhook.subscription_created", "context": {"customer_id": customer_id, "stripe_customer_id": stripe_customer_id, "tier": obj.get("metadata", {}).get("tier", "developer")}},
            )

        elif event_type in {"customer.subscription.deleted", "customer.subscription.paused"}:
            update_customer_status(obj.get("customer"), "inactive")
            logger.info("Subscription marked inactive", extra={"event": "billing.webhook.subscription_inactive", "context": {"stripe_customer_id": obj.get("customer"), "event_type": event_type}})

        elif event_type == "customer.subscription.updated":
            status = obj.get("status")
            new_status = "active" if status == "active" else "inactive"
            update_customer_status(obj.get("customer"), new_status)
            logger.info("Subscription status updated", extra={"event": "billing.webhook.subscription_updated", "context": {"stripe_customer_id": obj.get("customer"), "status": new_status}})

        elif event_type == "invoice.payment_failed":
            update_customer_status(obj.get("customer"), "inactive")
            logger.warning("Payment failed", extra={"event": "billing.webhook.payment_failed", "context": {"stripe_customer_id": obj.get("customer")}})

        else:
            logger.info("Unhandled webhook event", extra={"event": "billing.webhook.unhandled", "context": {"event_type": event_type}})

        return JSONResponse({"status": "ok"})
    except Exception as exc:
        logger.exception("Webhook event handler failed: %s", exc)
        return JSONResponse({"status": "error", "error": "Webhook handler failed"}, status_code=200)


@router.get("/billing/success")
async def checkout_success(session_id: Optional[str] = None):
    return {
        "status": "success",
        "message": "Subscription activated! Check your email for your API key.",
        "next_steps": [
            "Check your email for your API key",
            "Use Authorization: Bearer <api_key> header to authenticate",
            "Visit /billing/portal to manage your subscription",
        ],
    }


@router.get("/billing/cancel")
async def checkout_cancel():
    return {
        "status": "cancelled",
        "message": "Checkout was cancelled. No charge was made.",
        "next_steps": [
            "Review the pricing and try again",
            "Contact support if you have questions",
        ],
    }
