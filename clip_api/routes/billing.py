import logging
from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from ..config import BASE_URL, STRIPE_WEBHOOK_SECRET
from ..services.stripe_service import (
    create_checkout_session,
    create_billing_portal_session,
    construct_webhook_event,
)
from ..services.key_manager import (
    create_customer,
    update_customer_status,
    validate_api_key,
    get_usage_stats,
    _get_db,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["billing"])

VALID_TIERS = ("developer", "professional", "enterprise")


class CheckoutRequest(BaseModel):
    tier: str
    email: Optional[str] = None

    class Config:
        examples = [{
            "tier": "professional",
            "email": "user@example.com"
        }]


class PortalRequest(BaseModel):
    api_key: str

    class Config:
        examples = [{"api_key": "clip_..."}]


@router.post("/billing/checkout")
async def create_checkout(body: CheckoutRequest):
    """Create a Stripe checkout session for subscribing to a tier.
    
    Returns the checkout URL and session ID.
    """
    if body.tier not in VALID_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier. Choose: {', '.join(VALID_TIERS)}",
        )

    try:
        session = create_checkout_session(
            tier=body.tier,
            success_url=f"{BASE_URL}/billing/success",
            cancel_url=f"{BASE_URL}/billing/cancel",
            customer_email=body.email,
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except ValueError as e:
        logger.warning(f"Checkout validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Checkout creation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.post("/billing/portal")
async def billing_portal(body: PortalRequest):
    """Get a Stripe billing portal URL for managing subscription.
    
    Requires valid API key.
    """
    # Validate the API key first
    customer = validate_api_key(body.api_key)
    if not customer:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    try:
        stripe_customer_id = customer.get("stripe_customer_id")
        if not stripe_customer_id:
            raise HTTPException(
                status_code=400,
                detail="No Stripe customer associated with this API key",
            )

        session = create_billing_portal_session(stripe_customer_id, BASE_URL)
        return {"portal_url": session.url}
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Portal validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Billing portal creation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create billing portal")


@router.get("/billing/usage")
async def get_usage(authorization: str = Header(...)):
    """Get current API usage for the authenticated customer.
    
    Requires: Authorization: Bearer <api_key>
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Use: Authorization: Bearer YOUR_API_KEY",
        )

    api_key = authorization[7:].strip()
    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    stats = get_usage_stats(api_key)
    if not stats:
        raise HTTPException(status_code=404, detail="Usage stats not found")

    return stats


@router.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook endpoint for subscription events.
    
    Handles: checkout completion, subscription status changes, payment failures.
    Must be registered in Stripe dashboard with correct signature secret.
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe signature header")

    try:
        event = construct_webhook_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        logger.warning(f"Webhook validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Webhook processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="Webhook verification failed")

    event_type = event.get("type")
    obj = event.get("data", {}).get("object", {})

    try:
        if event_type == "checkout.session.completed":
            customer_id, api_key = create_customer(
                stripe_customer_id=obj.get("customer"),
                email=obj.get("customer_details", {}).get("email", "unknown"),
                tier=obj.get("metadata", {}).get("tier", "developer"),
                subscription_id=obj.get("subscription"),
            )
            logger.info(
                f"New subscription: customer_id={customer_id} "
                f"stripe_id={obj.get('customer')} tier={obj.get('metadata', {}).get('tier')} "
                f"email={obj.get('customer_details', {}).get('email')}"
            )

        elif event_type in (
            "customer.subscription.deleted",
            "customer.subscription.paused",
        ):
            update_customer_status(obj.get("customer"), "inactive")
            logger.info(
                f"Subscription status change (inactive): {obj.get('customer')} "
                f"event={event_type}"
            )

        elif event_type == "customer.subscription.updated":
            status = obj.get("status")
            new_status = "active" if status == "active" else "inactive"
            update_customer_status(obj.get("customer"), new_status)
            logger.info(
                f"Subscription updated: {obj.get('customer')} status={new_status}"
            )

        elif event_type == "invoice.payment_failed":
            update_customer_status(obj.get("customer"), "inactive")
            logger.warning(f"Payment failed: {obj.get('customer')}")

        else:
            logger.debug(f"Unhandled webhook event: {event_type}")

        return JSONResponse({"status": "ok"})

    except Exception as e:
        logger.error(f"Webhook event handler failed: {e}", exc_info=True)
        # Return 200 to prevent Stripe retries, but log the error
        return JSONResponse({"status": "error", "error": str(e)}, status_code=200)


@router.get("/billing/success")
async def checkout_success(session_id: Optional[str] = None):
    """Landing page after successful checkout."""
    return {
        "status": "success",
        "message": "Subscription activated! Check your email for your API key.",
        "next_steps": [
            "Check your email for your API key",
            "Use Authorization: Bearer <key> header to authenticate",
            "Visit /billing/portal to manage your subscription",
        ],
    }


@router.get("/billing/cancel")
async def checkout_cancel():
    """Landing page after cancelled checkout."""
    return {
        "status": "cancelled",
        "message": "Checkout was cancelled. No charge was made.",
        "next_steps": [
            "Review the pricing and try again",
            "Contact support if you have questions",
        ],
    }

