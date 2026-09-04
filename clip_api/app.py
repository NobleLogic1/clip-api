import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import LOG_LEVEL, validate_startup_config
from .routes import billing, encode, similarity
from .services.clip_model import clip_model_service
from .services.key_manager import close_db, init_db
from .utils.auth import require_api_key
from .utils.logging import setup_logging

logger = logging.getLogger(__name__)
setup_logging(LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    startup_status = validate_startup_config()
    logger.info(
        "Startup diagnostics",
        extra={"event": "startup.diagnostics", "context": startup_status},
    )
    if startup_status["missing"]:
        logger.warning("Startup configuration issues detected", extra={"event": "startup.missing_config", "context": {"missing": startup_status["missing"]}})

    try:
        clip_model_service.load_model()
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("CLIP model load failed during startup: %s", exc)

    yield
    close_db()
    logger.info("Application shutdown complete", extra={"event": "shutdown.complete"})


app = FastAPI(
    title="CLIP Maintained API",
    description="Production-ready CLIP embedding API by NobleLogic",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("Unhandled request error", extra={"event": "request.error", "context": {"path": request.url.path, "method": request.method}})
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Request completed",
        extra={"event": "request.completed", "context": {"path": request.url.path, "method": request.method, "status_code": response.status_code, "elapsed_ms": round(elapsed_ms, 2)}},
    )
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "http_error",
                "message": exc.detail if isinstance(exc.detail, str) else "Request failed",
                "details": exc.detail if not isinstance(exc.detail, str) else None,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": "Request validation failed", "details": jsonable_encoder(exc.errors())}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server error", extra={"event": "request.unhandled_exception", "context": {"path": request.url.path, "method": request.method}})
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_server_error", "message": "Unexpected server error"}},
    )


# Public routes
app.include_router(billing.router)

# Gated routes
app.include_router(encode.router, prefix="/embed", dependencies=[Depends(require_api_key)])
app.include_router(similarity.router, prefix="", dependencies=[Depends(require_api_key)])


@app.get("/health")
async def health():
    startup_status = validate_startup_config()
    return {
        "status": "ok",
        "model_loaded": clip_model_service.is_loaded,
        "device": str(clip_model_service.device),
        "stripe_configured": startup_status["stripe_configured"],
        "db_path": startup_status["db_path"],
        "missing_config": startup_status["missing"],
    }
