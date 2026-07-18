from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.clip_model import clip_model_service

router = APIRouter()


class EncodeTextRequest(BaseModel):
    text: str


class EncodeImageRequest(BaseModel):
    image_url: str


@router.post("/text")
async def encode_text(req: EncodeTextRequest):
    if not clip_model_service.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    if not req.text:
        raise HTTPException(status_code=400, detail="No text input provided.")
    try:
        result = clip_model_service.encode_texts([req.text])
        embedding = result["embeddings"][0]
        return {"embedding": embedding, "dimensions": len(embedding)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/image")
async def encode_image(req: EncodeImageRequest):
    if not clip_model_service.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    try:
        result = clip_model_service.encode_image(req.image_url)
        embedding = result["embeddings"][0]
        return {"embedding": embedding, "dimensions": len(embedding)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
