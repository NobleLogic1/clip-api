import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from ..config import (
    BASE_URL,
    EMAIL_DEV_MODE,
    RESEND_API_KEY,
    STRIPE_WEBHOOK_SECRET,
    TIER_LIMITS,
    VALID_TIERS,
)
from ..services.email_service import send_free_key_verification, verification_url
from ..services.key_manager import (
    create_customer,
    create_verification_token,
    get_existing_free_key,
    get_usage_stats,
    update_customer_status,
    validate_api_key,
    verify_and_create_free_key,
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
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


@router.post("/billing/free-key")
async def request_free_api_key(body: FreeKeyRequest, request: Request):
    """
    Start free-key signup. Requires email verification.

    - If this email already has a free key → return status (no new key, no email).
    - Otherwise → send magic link; key is only created after the user clicks it.
    """
    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    client_ip = _client_ip(request)

    # Already has a free key — do not send another, do not issue a new one
    existing = get_existing_free_key(email)
    if existing:
        msg = (
            "You already have a free API key for this email. "
            "Quota does not reset by requesting again."
        )
        if existing["calls_remaining"] == 0:
            msg = (
                "Your free quota for this month is exhausted. "
                "Upgrade to Developer ($29/mo) to continue. "
                "A new free key will not increase your limit."
            )
        return {
            "status": "already_exists",
            "tier": "free",
            "email": email,
            "monthly_limit": existing["calls_limit"],
            "calls_used": existing["calls_used"],
            "calls_remaining": existing["calls_remaining"],
            # Intentionally do NOT re-send the full key here after first issue
            "message": msg,
        }

    try:
        token = create_verification_token(email, client_ip=client_ip)
    except ValueError as exc:
        raise HTTPException(
            status_code=429 if "Too many" in str(exc) else 400,
            detail=str(exc),
        ) from exc

    sent = send_free_key_verification(email, token)

    response = {
        "status": "verification_sent",
        "email": email,
        "message": (
            "Check your email for a verification link. "
            "Your free API key will be activated after you confirm."
        ),
    }

    # Dev convenience only — never enable EMAIL_DEV_MODE in production
    if not sent and EMAIL_DEV_MODE:
        response["verification_url"] = verification_url(token)
        response["message"] += " (dev mode: verification_url included because RESEND_API_KEY is not set)"
    elif not sent and not RESEND_API_KEY:
        logger.error("Free key requested but RESEND_API_KEY is not configured")
        raise HTTPException(
            status_code=503,
            detail="Email delivery is not configured. Please contact support.",
        )
    elif not sent:
        raise HTTPException(
            status_code=502,
            detail="Failed to send verification email. Please try again shortly.",
        )

    return response


@router.get("/billing/verify-free", response_class=HTMLResponse)
async def verify_free_email(token: str):
    """Magic-link landing page — activates the free key and displays it once."""
    try:
        api_key, meta = verify_and_create_free_key(token)
    except ValueError as exc:
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html><head><meta charset="utf-8"/><title>Verification failed</title>
            <style>body{{font-family:system-ui;background:#0a0a0a;color:#f4f4f5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
            .card{{background:#111;border:1px solid #27272a;border-radius:16px;padding:32px;max-width:420px;text-align:center}}
            a{{color:#818cf8}}</style></head>
            <body><div class="card">
              <h2>Verification failed</h2>
              <p style="color:#a1a1aa">{exc}</p>
              <p><a href="https://noblelogicllc.com">Return to site</a></p>
            </div></body></html>
            """,
            status_code=400,
        )

    remaining = meta.get("calls_remaining", TIER_LIMITS["free"])
    limit = meta.get("calls_limit", TIER_LIMITS["free"])

    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
        <title>Your free CLIP API key</title>
        <style>
          body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0a0a;color:#f4f4f5;
               display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px}}
          .card{{background:#111;border:1px solid #27272a;border-radius:16px;padding:32px;max-width:520px;width:100%}}
          h1{{font-size:1.35rem;margin:0 0 8px}}
          p{{color:#a1a1aa;line-height:1.55;margin:0 0 16px}}
          code{{display:block;background:#0d0d0d;border:1px solid #27272a;border-radius:10px;
                padding:14px 16px;word-break:break-all;font-size:0.85rem;color:#22c55e;margin:12px 0 8px}}
          .hint{{font-size:0.8rem;color:#71717a}}
          a{{color:#818cf8}}
        </style></head>
        <body><div class="card">
          <h1>Free API key activated</h1>
          <p>Copy this key now. For security it is only shown on this page.</p>
          <code id="key">{api_key}</code>
          <p class="hint">{limit:,} calls/month · {remaining:,} remaining this month</p>
          <p class="hint">Use header: <strong>Authorization: Bearer {api_key}</strong></p>
          <p style="margin-top:24px"><a href="https://github.com/NobleLogic1/clip-api-public">View docs on GitHub →</a></p>
        </div>
        <script>
          // Optional: copy on click
          document.getElementById('key').addEventListener('click', function() {{
            navigator.clipboard.writeText(this.textContent);
            this.style.outline = '1px solid #6366f1';
          }});
        </script>
        </body></html>
        """
    )


@router.post("/billing/checkout")
async def create_checkout(body: CheckoutRequest):
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Checkout creation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create checkout session") from exc


@router.post("/billing/portal")
async def billing_portal(body: PortalRequest):
    customer = validate_api_key(body.api_key)
    if not customer:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    try:
        stripe_customer_id = customer.get("stripe_customer_id")
        if not stripe_customer_id or str(stripe_customer_id).startswith("free_"):
            raise HTTPException(
                status_code=400,
                detail="No Stripe customer associated with this API key (Free tier has no billing portal)",
            )
        session = create_billing_portal_session(stripe_customer_id, BASE_URL)
        return {"portal_url": session.url}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Billing portal creation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create billing portal") from exc


@router.get("/billing/usage")
async def get_usage(authorization: str = Header(...)):
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
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe signature header")

    try:
        event = construct_webhook_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Webhook verification failed") from exc

    event_type = event.get("type")
    obj = event.get("data", {}).get("object", {})

    try:
        if event_type == "checkout.session.completed":
            stripe_customer_id = obj.get("customer")
            if not stripe_customer_id:
                return JSONResponse({"status": "ignored", "reason": "missing_customer_id"})
            create_customer(
                stripe_customer_id=stripe_customer_id,
                email=obj.get("customer_details", {}).get("email", "unknown"),
                tier=obj.get("metadata", {}).get("tier", "developer"),
                subscription_id=obj.get("subscription"),
            )
        elif event_type in {"customer.subscription.deleted", "customer.subscription.paused"}:
            update_customer_status(obj.get("customer"), "inactive")
        elif event_type == "customer.subscription.updated":
            status = obj.get("status")
            update_customer_status(obj.get("customer"), "active" if status == "active" else "inactive")
        elif event_type == "invoice.payment_failed":
            update_customer_status(obj.get("customer"), "inactive")
        return JSONResponse({"status": "ok"})
    except Exception as exc:
        logger.exception("Webhook event handler failed: %s", exp)
        return JSONResponse({"status": "error"}, status_code=200)


@router.get("/billing/success")
async def checkout_success(session_id: Optional[str] = None):
    return {
        "status": "success",
        "message": "Subscription activated! Check your email for your API key.",
    }


@router.get("/billing/cancel")
async def checkout_cancel():
    return {
        "status": "cancelled",
        "message": "Checkout was cancelled. No charge was made.",
    }
