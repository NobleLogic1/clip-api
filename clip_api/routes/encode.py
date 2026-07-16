from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

from ..services.clip_model import clip_model_service

router = APIRouter()


class EncodeTextRequest(BaseModel):
    api_key: str
    texts: List[str]


class EncodeImageRequest(BaseModel):
    api_key: str
    image_url: str


@router.post("/text")
async def encode_text(req: EncodeTextRequest):
    if not clip_model_service.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    if not req.texts:
        raise HTTPException(status_code=400, detail="No text inputs provided.")
    try:
        result = clip_model_service.encode_texts(req.texts)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/image")
async def encode_image(req: EncodeImageRequest):
    if not clip_model_service.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    try:
        result = clip_model_service.encode_image(req.image_url)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
