from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from ..services.key_manager import validate_api_key, check_and_increment_usage

security = HTTPBearer()

async def require_api_key(
        request: Request,
        credentials: HTTPAuthorizationCredentials = security.__call__,
):
    api_key = credentials.credentials if credentials else None
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Authorization: Bearer YOUR_API_KEY",
            headers={"WWW-Authenticate": "Bearer"},
        )
    customer = validate_api_key(api_key)
    if not customer:
        raise HTTPException(
            status_code=401,
            detail="Invalid or inactive API key. Subscribe at noblelogic.ai",
        )
    allowed, used, limit = check_and_increment_usage(api_key, customer["tier"])
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly limit reached ({limit:,} calls on {customer['tier']} plan). Upgrade at noblelogic.ai",
            headers={"Retry-After": "2592000"},
        )
    request.state.customer = customer
    request.state.api_key = api_key
