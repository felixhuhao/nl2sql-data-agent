from __future__ import annotations

import logging
from dataclasses import asdict

from backend.app.config import get_settings
from backend.app.metadata.vector.embedding import get_embedding_dimension
from backend.app.metadata.vector.indexer import rebuild_vector_index
from backend.app.metadata.vector.store import get_vector_store


logger = logging.getLogger(__name__)


def get_vector_index_status() -> dict:
    settings = get_settings()
    payload = {
        "vector_enabled": settings.vector_enabled,
        "status": "disabled",
        "embedding_model": settings.embedding_model,
        "embedding_dimension": None,
        "built_at": None,
        "asset_counts": {},
        "stale_reason": None,
        "qdrant_url": settings.qdrant_url,
        "qdrant_collection_prefix": settings.qdrant_collection_prefix,
    }
    if not settings.vector_enabled:
        return payload
    if not settings.embedding_model:
        return {
            **payload,
            "stale_reason": "EMBEDDING_MODEL is not configured.",
        }

    try:
        embedding_dimension = settings.embedding_dimension or get_embedding_dimension()
        status = get_vector_store().status(
            expected_model=settings.embedding_model,
            expected_dimension=embedding_dimension,
        )
    except Exception as exc:
        return {
            **payload,
            "status": "error",
            "stale_reason": str(exc),
        }

    return {
        **payload,
        "status": status.status,
        "embedding_dimension": status.embedding_dimension or embedding_dimension,
        "built_at": status.built_at,
        "asset_counts": status.asset_counts,
        "stale_reason": status.stale_reason,
    }


def rebuild_vector_index_payload() -> dict:
    return asdict(rebuild_vector_index())


def mark_vector_index_stale(reason: str) -> None:
    settings = get_settings()
    if not settings.vector_enabled:
        return
    try:
        get_vector_store().mark_stale(reason)
    except Exception:
        logger.exception("Failed to mark vector index stale")
