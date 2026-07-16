import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from .services.clip_model import load_model
from .services.key_manager import init_db
from .utils.auth import require_api_key
from .routes import encode, similarity, billing

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()        # create SQLite tables
    load_model()     # warm up CLIP
    yield

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

# Public routes
app.include_router(billing.router)

# Gated routes  require active subscription
app.include_router(encode.router,     dependencies=[Depends(require_api_key)])
app.include_router(similarity.router, dependencies=[Depends(require_api_key)])

@app.get("/health")
async def health():
        from .services.clip_model import model
        return {
            "status": "ok",
            "model_loaded": model is not None,
            "device": "cpu",
        }
