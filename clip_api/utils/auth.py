import logging
from collections import defaultdict, deque
from time import time
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import RATE_LIMIT_ENABLED, RATE_LIMIT_REQUESTS_PER_MINUTE
from ..services.key_manager import check_and_increment_usage, validate_api_key

logger = logging.getLogger(__name__)
security = HTTPBearer()
_recent_requests = defaultdict(deque)


def _get_client_id(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(request: Request) -> bool:
    if not RATE_LIMIT_ENABLED:
        return False

    client_id = _get_client_id(request)
    now = time()
    window = _recent_requests[client_id]
    while window and now - window[0] > 60:
        window.popleft()

    if len(window) >= RATE_LIMIT_REQUESTS_PER_MINUTE:
        logger.warning(
            "Rate limit exceeded",
            extra={"event": "auth.rate_limited", "context": {"client_id": client_id, "limit": RATE_LIMIT_REQUESTS_PER_MINUTE}},
        )
        return True

    window.append(now)
    return False


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    token_value = credentials.credentials.strip() if credentials and credentials.credentials else None
    if not token_value:
        raise HTTPException(
            status_code=401,
            detail="API key required. Use: Authorization: ******",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if _check_rate_limit(request):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.", headers={"Retry-After": "60"})

    if not token_value.startswith("clip_") or len(token_value) < 20:
        logger.warning("Rejected malformed API key", extra={"event": "auth.invalid_format", "context": {"reason": "malformed"}})
        raise HTTPException(status_code=401, detail="Malformed API key format", headers={"WWW-Authenticate": "Bearer"})

    customer = validate_api_key(token_value)
    if not customer:
        logger.warning(
            "Authentication failed",
            extra={"event": "auth.failed", "context": {"client_id": _get_client_id(request)}},
        )
        raise HTTPException(status_code=401, detail="Invalid or inactive API key. Subscribe at noblelogic.ai")

    allowed, used, limit = check_and_increment_usage(token_value, customer["tier"])
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly limit reached ({limit:,} calls on {customer['tier']} plan). Upgrade at noblelogic.ai",
            headers={"Retry-After": "2592000"},
        )
    request.state.customer = customer
    request.state.api_key = token_value
    request.state.usage_used = used
