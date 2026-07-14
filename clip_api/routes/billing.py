import os
from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from services.stripe_service import (
    create_checkout_session, create_billing_portal_session, construct_webhook_event
)
from services.key_manager import (
    create_customer, update_customer_status, validate_api_key,
    get_usage_stats, _get_db
)

router = APIRouter(prefix="/billing", tags=["billing"])

BASE_URL = os.getenv("BASE_URL", "https://web-production-58f81.up.railway.app")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

VALID_TIERS = ("developer", "professional", "enterprise")

class CheckoutRequest(BaseModel):
    tier: str
    email: Optional[str] = None

class PortalRequest(BaseModel):
    api_key: str

@router.post("/create-checkout-session")
async def create_checkout(body: CheckoutRequest):
    if body.tier not in VALID_TIERS:
        raise HTTPException(400, f"Invalid tier. Choose: {', '.join(VALID_TIERS)}")
    try:
        session = create_checkout_session(
            tier=body.tier,
            success_url=f"{BASE_URL}/billing/success",
            cancel_url=f"{BASE_URL}/billing/cancel",
            customer_email=body.email,
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as e:
        raise HTTPException(500, str(e))

@router.post("/portal")
async def billing_portal(body: PortalRequest):
    conn = _get_db()
    row = conn.execute("""
        SELECT c.stripe_customer_id FROM api_keys ak
        JOIN customers c ON ak.customer_id = c.id WHERE ak.key = ?
    """, (body.api_key,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "API key not found")
    try:
        session = create_billing_portal_session(row["stripe_customer_id"], BASE_URL)
        return {"portal_url": session.url}
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/usage")
async def get_usage(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Use: Authorization: Bearer YOUR_API_KEY")
    api_key = authorization[7:].strip()
    if not validate_api_key(api_key):
        raise HTTPException(401, "Invalid or inactive API key")
    return get_usage_stats(api_key)

@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not sig:
        raise HTTPException(400, "Missing Stripe signature")
    try:
        event = construct_webhook_event(payload, sig, WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(400, f"Webhook error: {e}")

    t = event["type"]
    obj = event["data"]["object"]

    if t == "checkout.session.completed":
        _, api_key = create_customer(
            stripe_customer_id=obj.get("customer"),
            email=obj.get("customer_details", {}).get("email", ""),
            tier=obj.get("metadata", {}).get("tier", "developer"),
            subscription_id=obj.get("subscription"),
        )
        # TODO: Email api_key to customer via SendGrid/Resend
        print(f"[BILLING] New subscriber: {obj.get('customer_details',{}).get('email')} "
              f"tier={obj.get('metadata',{}).get('tier')} key={api_key}")

    elif t in ("customer.subscription.deleted", "customer.subscription.paused"):
        update_customer_status(obj.get("customer"), "inactive")

    elif t == "customer.subscription.updated":
        status = obj.get("status")
        new_status = "active" if status == "active" else "inactive"
        update_customer_status(obj.get("customer"), new_status)

    elif t == "invoice.payment_failed":
        update_customer_status(obj.get("customer"), "inactive")

    return JSONResponse({"status": "ok"})

@router.get("/success")
async def checkout_success(session_id: Optional[str] = None):
    return {
        "message": "Subscription activated! Your API key has been sent to your email.",
        "next": "Use your key in the Authorization: Bearer <key> header."
    }

@router.get("/cancel")
async def checkout_cancel():
    return {"message": "Checkout cancelled. No charge was made."}
