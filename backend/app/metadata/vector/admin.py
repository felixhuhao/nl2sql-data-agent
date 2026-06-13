from __future__ import annotations

import logging
from dataclasses import asdict

from backend.app.config import embedding_model_name, get_settings, vector_config_allows_attempt, vector_enabled_mode
from backend.app.metadata.vector.indexer import rebuild_vector_index
from backend.app.metadata.vector.store import get_vector_store


logger = logging.getLogger(__name__)


def get_vector_index_status() -> dict:
    settings = get_settings()
    vector_allowed = vector_config_allows_attempt(settings)
    expected_model = embedding_model_name(settings)
    expected_dimension = getattr(settings, "embedding_dimension", None)
    payload = {
        "vector_enabled": False,
        "vector_mode": vector_enabled_mode(settings),
        "status": "disabled",
        "embedding_model": expected_model,
        "embedding_dimension": None,
        "built_at": None,
        "asset_counts": {},
        "stale_reason": None,
        "qdrant_url": settings.qdrant_url,
        "qdrant_collection_prefix": settings.qdrant_collection_prefix,
    }
    if not vector_allowed:
        return payload

    try:
        status = get_vector_store().status(
            expected_model=expected_model,
            expected_dimension=expected_dimension,
        )
    except Exception as exc:
        return {
            **payload,
            "stale_reason": f"Vector index is unavailable: {exc}",
        }

    return {
        **payload,
        "vector_enabled": True,
        "status": status.status,
        "embedding_model": status.embedding_model or expected_model,
        "embedding_dimension": status.embedding_dimension or expected_dimension,
        "built_at": status.built_at,
        "asset_counts": status.asset_counts,
        "stale_reason": status.stale_reason,
    }


def rebuild_vector_index_payload() -> dict:
    return asdict(rebuild_vector_index())


def mark_vector_index_stale(reason: str) -> None:
    settings = get_settings()
    if not vector_config_allows_attempt(settings):
        return
    try:
        get_vector_store().mark_stale(reason)
    except Exception:
        logger.exception("Failed to mark vector index stale")
