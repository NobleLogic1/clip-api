import logging
from typing import List
from urllib.parse import urlparse

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..services.clip_model import clip_model_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["similarity"])


class CompareEmbeddingsRequest(BaseModel):
    """Validate two embedding vectors for pairwise similarity comparison."""
    embedding_a: List[float] = Field(..., min_items=1, description="First embedding vector")
    embedding_b: List[float] = Field(..., min_items=1, description="Second embedding vector")


def _interpret_similarity(score: float) -> str:
    if score < 0.2:
        return "very low"
    if score < 0.4:
        return "low"
    if score < 0.6:
        return "moderate"
    if score < 0.8:
        return "high"
    return "very high"


@router.post("/similarity")
async def compare_embeddings(req: CompareEmbeddingsRequest):
    """Compare two embeddings and return a cosine similarity score with interpretation."""
    if len(req.embedding_a) != len(req.embedding_b):
        raise HTTPException(status_code=400, detail="Embeddings must have the same number of dimensions.")

    try:
        vec_a = np.array(req.embedding_a, dtype=float)
        vec_b = np.array(req.embedding_b, dtype=float)

        norm_a = np.linalg.norm(vec_a)
        norm_b = np.linalg.norm(vec_b)
        if norm_a == 0 or norm_b == 0:
            raise ValueError("Embeddings must not be zero vectors")

        similarity = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))

        return {
            "similarity": similarity,
            "interpretation": _interpret_similarity(similarity),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Similarity comparison failed",
            extra={"event": "similarity.compare.failed", "context": {"error_type": type(exc).__name__}},
        )
        raise HTTPException(status_code=500, detail="Similarity comparison failed. Please try again.") from exc


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
