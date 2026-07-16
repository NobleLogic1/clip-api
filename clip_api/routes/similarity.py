from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

from ..services.clip_model import clip_model_service

router = APIRouter()


class SimilaritySearchRequest(BaseModel):
    api_key: str
    query: str
    image_urls: List[str]


@router.post("/search")
async def similarity_search(req: SimilaritySearchRequest):
    if not clip_model_service.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    if not req.image_urls:
        raise HTTPException(status_code=400, detail="No image URLs provided.")
    try:
        result = clip_model_service.similarity_search(req.query, req.image_urls)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
