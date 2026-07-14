from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

from ..services.clip_model import clip_model_service
from ..services.key_manager import key_manager
from ..utils.exceptions import InvalidAPIKeyError, RateLimitError, ModelNotLoadedError

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
        raise ModelNotLoadedError()
    ok, msg = key_manager.check_rate_limit(req.api_key)
    if not ok:
        if "Invalid" in msg:
            raise InvalidAPIKeyError()
        raise RateLimitError(msg)
    if not req.texts:
        raise HTTPException(status_code=400, detail="No text inputs provided.")
    try:
        result = clip_model_service.encode_texts(req.texts)
        key_manager.increment_usage(req.api_key, len(req.texts))
        remaining = key_manager.remaining_requests(req.api_key)
        return {**result, "remaining_requests": remaining}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/image")
async def encode_image(req: EncodeImageRequest):
    if not clip_model_service.is_loaded:
        raise ModelNotLoadedError()
    ok, msg = key_manager.check_rate_limit(req.api_key)
    if not ok:
        if "Invalid" in msg:
            raise InvalidAPIKeyError()
        raise RateLimitError(msg)
    try:
        result = clip_model_service.encode_image(req.image_url)
        key_manager.increment_usage(req.api_key, 1)
        remaining = key_manager.remaining_requests(req.api_key)
        return {**result, "remaining_requests": remaining}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
