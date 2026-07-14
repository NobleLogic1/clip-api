from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

from ..services.clip_model import clip_model_service
from ..services.key_manager import key_manager
from ..utils.exceptions import InvalidAPIKeyError, RateLimitError, ModelNotLoadedError

router = APIRouter()


class SimilaritySearchRequest(BaseModel):
    api_key: str
    query: str
    image_urls: List[str]


@router.post("/search")
async def similarity_search(req: SimilaritySearchRequest):
    if not clip_model_service.is_loaded:
        raise ModelNotLoadedError()
    ok, msg = key_manager.check_rate_limit(req.api_key)
    if not ok:
        if "Invalid" in msg:
            raise InvalidAPIKeyError()
        raise RateLimitError(msg)
    if not req.image_urls:
        raise HTTPException(status_code=400, detail="No image URLs provided.")
    try:
        result = clip_model_service.similarity_search(req.query, req.image_urls)
        key_manager.increment_usage(req.api_key, len(req.image_urls))
        remaining = key_manager.remaining_requests(req.api_key)
        return {**result, "remaining_requests": remaining}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
