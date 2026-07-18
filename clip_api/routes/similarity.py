import logging
from typing import List
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..services.clip_model import clip_model_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["similarity"])


class SimilaritySearchRequest(BaseModel):
    """Validate similarity search input with strict constraints."""
    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Search query text (1-500 characters)",
    )
    image_urls: List[str] = Field(
        ...,
        min_items=1,
        max_items=100,
        description="Image URLs to search (1-100 URLs)",
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        """Validate query is non-empty and non-whitespace."""
        if not v or not v.strip():
            raise ValueError("Query cannot be empty or whitespace only")
        return v.strip()

    @field_validator("image_urls")
    @classmethod
    def validate_image_urls(cls, urls: List[str]) -> List[str]:
        """Validate all URLs are well-formed and unique."""
        if not urls:
            raise ValueError("At least one image URL is required")

        validated_urls = []
        seen_urls = set()

        for i, url in enumerate(urls):
            if not isinstance(url, str) or not url.strip():
                raise ValueError(f"URL at index {i} is empty or not a string")

            url = url.strip()

            # Parse and validate URL structure
            try:
                parsed = urlparse(url)
                if not parsed.scheme or parsed.scheme not in ("http", "https"):
                    raise ValueError(f"URL at index {i} must use HTTP(S)")
                if not parsed.netloc:
                    raise ValueError(f"URL at index {i} has invalid hostname")
            except Exception as e:
                raise ValueError(f"URL at index {i} is malformed: {str(e)}")

            # Check for duplicates
            if url in seen_urls:
                logger.warning(
                    "Duplicate URL provided",
                    extra={
                        "event": "similarity.validation.duplicate_url",
                        "context": {"url_index": i},
                    },
                )
                continue  # Skip duplicates

            seen_urls.add(url)
            validated_urls.append(url)

        if not validated_urls:
            raise ValueError("No valid image URLs provided after deduplication")

        return validated_urls


@router.post("/search")
async def similarity_search(req: SimilaritySearchRequest):
    """Search for images similar to a query using CLIP embeddings.

    Validates input, checks model availability, and returns ranked results with confidence scores.
    """
    # Check model is ready
    if not clip_model_service.is_loaded:
        logger.error("Similarity search attempted while model not loaded", extra={"event": "similarity.model_unavailable"})
        raise HTTPException(status_code=503, detail="Model not loaded. Please retry.")

    logger.info(
        "Similarity search started",
        extra={
            "event": "similarity.search.started",
            "context": {"query_length": len(req.query), "image_count": len(req.image_urls)},
        },
    )

    try:
        # Call CLIP model service with validated inputs
        result = clip_model_service.similarity_search(req.query, req.image_urls)

        logger.info(
            "Similarity search completed",
            extra={
                "event": "similarity.search.completed",
                "context": {
                    "query_length": len(req.query),
                    "image_count": len(req.image_urls),
                    "result_count": len(result.get("results", [])),
                },
            },
        )

        return result

    except ValueError as exc:
        # Model validation error (e.g., invalid image URLs, processing failed)
        logger.warning(
            "Similarity search validation error",
            extra={
                "event": "similarity.search.validation_failed",
                "context": {"error": str(exc)},
            },
        )
        raise HTTPException(status_code=400, detail=f"Search validation failed: {str(exc)}") from exc

    except TimeoutError as exc:
        # Model processing timeout
        logger.warning(
            "Similarity search timeout",
            extra={
                "event": "similarity.search.timeout",
                "context": {"error": str(exc)},
            },
        )
        raise HTTPException(status_code=504, detail="Search took too long. Please try with fewer images.") from exc

    except MemoryError as exc:
        # Out of memory during processing
        logger.error(
            "Similarity search out of memory",
            extra={
                "event": "similarity.search.memory_error",
                "context": {"image_count": len(req.image_urls)},
            },
        )
        raise HTTPException(status_code=507, detail="Server memory exhausted. Please try with fewer images.") from exc

    except Exception as exc:
        # Unexpected error
        logger.exception(
            "Similarity search failed",
            extra={
                "event": "similarity.search.failed",
                "context": {"error_type": type(exc).__name__},
            },
        )
        raise HTTPException(status_code=500, detail="Similarity search failed. Please try again.") from exc
