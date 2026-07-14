from fastapi import FastAPI

from .routes.encode import router as encode_router
from .routes.similarity import router as similarity_router
from .routes.billing import router as billing_router
from .services.clip_model import clip_model_service
from .services.key_manager import key_manager
from .services.stripe_service import stripe_service
from .utils.logging import setup_logging

setup_logging()


def create_app() -> FastAPI:
    app = FastAPI(
        title="CLIP Maintained API",
        version="1.0.0",
        description="Modularized CLIP API with text/image encoding, similarity search, and Stripe billing.",
    )

    clip_model_service.load_model()

    app.include_router(encode_router, prefix="/encode", tags=["encode"])
    app.include_router(similarity_router, prefix="/similarity", tags=["similarity"])
    app.include_router(billing_router, prefix="/billing", tags=["billing"])

    @app.get("/health", tags=["health"])
    async def health():
        return {
            "status": "ok" if clip_model_service.is_loaded else "degraded",
            "device": str(clip_model_service.device),
            "model_loaded": clip_model_service.is_loaded,
        }

    return app


app = create_app()
