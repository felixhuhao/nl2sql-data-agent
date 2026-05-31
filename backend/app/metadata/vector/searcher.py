from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.config import get_settings
from backend.app.metadata.vector.embedding import embed_text, get_embedding_dimension
from backend.app.metadata.vector.store import VectorIndexStatus, VectorSearchHit, get_vector_store


DEFAULT_VECTOR_LIMIT = 10


@dataclass(frozen=True)
class VectorRetrievalResult:
    vector_used: bool
    index_status: str
    hits: dict[str, list[VectorSearchHit]] = field(default_factory=dict)
    stale_reason: str | None = None

    def has_hits(self) -> bool:
        return any(self.hits.values())


def retrieve_vector_assets(question: str, *, limit: int = DEFAULT_VECTOR_LIMIT) -> VectorRetrievalResult:
    settings = get_settings()
    if not settings.vector_enabled:
        return VectorRetrievalResult(vector_used=False, index_status="disabled")
    if not settings.embedding_model:
        return VectorRetrievalResult(
            vector_used=False,
            index_status="disabled",
            stale_reason="EMBEDDING_MODEL is not configured.",
        )

    try:
        embedding_dimension = get_embedding_dimension()
        vector_store = get_vector_store()
        status = vector_store.status(
            expected_model=settings.embedding_model,
            expected_dimension=embedding_dimension,
        )
    except Exception as exc:
        return VectorRetrievalResult(
            vector_used=False,
            index_status="error",
            stale_reason=str(exc),
        )

    if status.status != "ready":
        return _result_from_status(status)

    query_vector = embed_text(question)
    if query_vector is None:
        return VectorRetrievalResult(vector_used=False, index_status="ready")

    threshold = settings.vector_similarity_threshold
    hits = {
        "tables": _filter_hits(vector_store.search("table_vectors", query_vector, limit=limit), threshold),
        "columns": _filter_hits(vector_store.search("column_vectors", query_vector, limit=limit), threshold),
        "metrics": _filter_hits(vector_store.search("metric_vectors", query_vector, limit=limit), threshold),
        "verified_queries": _filter_hits(
            vector_store.search("verified_query_vectors", query_vector, limit=limit),
            threshold,
        ),
    }
    return VectorRetrievalResult(vector_used=True, index_status="ready", hits=hits)


def _filter_hits(hits: list[VectorSearchHit], threshold: float) -> list[VectorSearchHit]:
    return [hit for hit in hits if hit.score >= threshold]


def _result_from_status(status: VectorIndexStatus) -> VectorRetrievalResult:
    return VectorRetrievalResult(
        vector_used=False,
        index_status=status.status,
        stale_reason=status.stale_reason,
    )
