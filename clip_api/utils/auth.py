import logging
from collections import defaultdict, deque
from time import time, time_ns
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import RATE_LIMIT_ENABLED, RATE_LIMIT_REQUESTS_PER_MINUTE, REDIS_URL
from ..services.key_manager import check_and_increment_usage, validate_api_key

logger = logging.getLogger(__name__)
security = HTTPBearer()
_recent_requests = defaultdict(deque)
_redis_rate_limit_client = None
_redis_rate_limit_enabled = False
_redis_init_attempted = False
_redis_connection_validated = False
_REDIS_RATE_LIMIT_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[3])
local count = redis.call('ZCARD', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return count
"""

try:
    import redis.asyncio as redis
except Exception:  # pragma: no cover - optional dependency
    redis = None


def _get_client_id(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _get_redis_rate_limit_client():
    global _redis_rate_limit_client, _redis_rate_limit_enabled, _redis_init_attempted, _redis_connection_validated
    if _redis_init_attempted:
        return _redis_rate_limit_client
    _redis_init_attempted = True
    if not REDIS_URL or redis is None:
        return None
    try:
        _redis_rate_limit_client = redis.from_url(REDIS_URL, decode_responses=True)
        _redis_rate_limit_enabled = True
        _redis_connection_validated = False
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Redis init failed; using in-memory rate limiter: %s", exc)
        _redis_rate_limit_client = None
    return _redis_rate_limit_client


async def _check_rate_limit(request: Request) -> bool:
    global _redis_rate_limit_enabled, _redis_connection_validated
    if not RATE_LIMIT_ENABLED:
        return False

    client_id = _get_client_id(request)
    redis_client = _get_redis_rate_limit_client()
    if redis_client is not None and _redis_rate_limit_enabled:
        if not _redis_connection_validated:
            try:
                await redis_client.ping()
                _redis_connection_validated = True
            except Exception as exc:
                logger.warning("Redis ping failed; falling back to in-memory limiter: %s", exc)
                redis_client = None
                _redis_connection_validated = False

    if redis_client is not None and _redis_rate_limit_enabled:
        now_ms = int(time() * 1000)
        window_start_ms = now_ms - 60_000
        key = f"clip_api:rate_limit:{client_id}"
        member = f"{now_ms}:{time_ns()}"
        try:
            count = await redis_client.eval(
                _REDIS_RATE_LIMIT_LUA,
                1,
                key,
                window_start_ms,
                now_ms,
                member,
                120,
            )
            if int(count) > RATE_LIMIT_REQUESTS_PER_MINUTE:
                logger.warning(
                    "Rate limit exceeded",
                    extra={"event": "auth.rate_limited", "context": {"client_id": client_id, "limit": RATE_LIMIT_REQUESTS_PER_MINUTE}},
                )
                return True
            return False
        except Exception as exc:
            logger.warning("Redis rate limiter unavailable; falling back to in-memory limiter: %s", exc)

    # Process-local fallback only; this does not enforce a shared limit across workers.
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

    if await _check_rate_limit(request):
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
