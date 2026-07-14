from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services.stripe_service import stripe_service
from ..services.key_manager import key_manager

router = APIRouter()


class CheckoutRequest(BaseModel):
    price_id: str
    success_url: str
    cancel_url: str


class KeyRequest(BaseModel):
    email: str


@router.post("/checkout")
async def create_checkout(req: CheckoutRequest):
    result, status_code = stripe_service.create_checkout(
        req.price_id, req.success_url, req.cancel_url
    )
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return result


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    result, status_code = stripe_service.process_webhook(payload, sig_header)
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return result


@router.post("/key")
async def get_or_create_key(req: KeyRequest):
    api_key = key_manager.create_api_key(req.email, tier="free")
    remaining = key_manager.remaining_requests(api_key)
    user = key_manager.validate_key(api_key)
    return {
        "api_key": api_key,
        "email": req.email,
        "tier": user.get("tier", "free"),
        "remaining_today": remaining,
    }


@router.get("/usage/{api_key}")
async def get_usage(api_key: str):
    user = key_manager.validate_key(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    remaining = key_manager.remaining_requests(api_key)
    return {**user, "remaining_today": remaining}
