from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.app.config import embedding_model_name, get_settings, vector_config_allows_attempt
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.vector.embedding import embed_text
from backend.app.metadata.vector.store import VectorIndexStatus, VectorSearchHit, get_vector_store


DEFAULT_VECTOR_LIMIT = 10
LOW_INFORMATION_VALUE_PATTERN = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
DATE_LITERAL_PATTERN = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


@dataclass(frozen=True)
class VectorRetrievalResult:
    vector_used: bool
    index_status: str
    hits: dict[str, list[VectorSearchHit]] = field(default_factory=dict)
    stale_reason: str | None = None
    query_vector: list[float] | None = None

    def has_hits(self) -> bool:
        return any(self.hits.values())


@dataclass(frozen=True)
class ValueHit:
    table_name: str
    column_name: str
    matched_value: str
    source: str
    score: float

    @property
    def column_asset_id(self) -> str:
        return f"{self.table_name}.{self.column_name}"


def retrieve_vector_assets(
    question: str,
    *,
    limit: int = DEFAULT_VECTOR_LIMIT,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> VectorRetrievalResult:
    settings = get_settings()
    if not vector_config_allows_attempt(settings):
        return VectorRetrievalResult(vector_used=False, index_status="disabled")
    expected_model = embedding_model_name(settings)
    expected_dimension = getattr(settings, "embedding_dimension", None)

    try:
        vector_store = get_vector_store()
        status = vector_store.status(
            expected_model=expected_model,
            expected_dimension=expected_dimension,
        )
    except Exception as exc:
        return VectorRetrievalResult(
            vector_used=False,
            index_status="disabled",
            stale_reason=f"Vector index is unavailable: {exc}",
        )

    if status.status != "ready":
        return _result_from_status(status)

    query_vector = embed_text(question)
    if query_vector is None:
        return VectorRetrievalResult(vector_used=False, index_status="ready")

    threshold = settings.vector_similarity_threshold
    datasource_filter = _datasource_filter(datasource_name)
    hits = {
        "tables": _filter_hits(
            vector_store.search("table_vectors", query_vector, limit=limit, where=datasource_filter),
            threshold,
        ),
        "columns": _filter_hits(
            vector_store.search("column_vectors", query_vector, limit=limit, where=datasource_filter),
            threshold,
        ),
        "metrics": _filter_hits(
            vector_store.search("metric_vectors", query_vector, limit=limit, where=datasource_filter),
            threshold,
        ),
        "verified_queries": _filter_hits(
            vector_store.search("verified_query_vectors", query_vector, limit=limit, where=datasource_filter),
            threshold,
        ),
    }
    return VectorRetrievalResult(vector_used=True, index_status="ready", hits=hits, query_vector=query_vector)


def search_values(
    question: str,
    *,
    query_vector: list[float] | None = None,
    limit: int = DEFAULT_VECTOR_LIMIT * 2,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> list[ValueHit]:
    settings = get_settings()
    if not vector_config_allows_attempt(settings):
        return []
    try:
        vector_store = get_vector_store()
        datasource_filter = _datasource_filter(datasource_name)
        exact_hits = _exact_value_hits(question, vector_store.list_values(where=datasource_filter))
    except Exception:
        return []

    vector_hits: list[ValueHit] = []
    if query_vector is None:
        try:
            query_vector = embed_text(question)
        except Exception:
            return _deduplicate_value_hits(exact_hits)
    if query_vector is not None:
        try:
            vector_hits = _vector_value_hits(
                vector_store.search("value_vectors", query_vector, limit=limit, where=datasource_filter),
                settings.value_vector_similarity_threshold,
            )
        except Exception:
            vector_hits = []
    return _deduplicate_value_hits([*exact_hits, *vector_hits])


def _filter_hits(hits: list[VectorSearchHit], threshold: float) -> list[VectorSearchHit]:
    return [hit for hit in hits if hit.score >= threshold]


def _result_from_status(status: VectorIndexStatus) -> VectorRetrievalResult:
    return VectorRetrievalResult(
        vector_used=False,
        index_status=status.status,
        stale_reason=status.stale_reason,
    )


def _exact_value_hits(question: str, hits: list[VectorSearchHit]) -> list[ValueHit]:
    normalized_question = _normalize_value(question)
    value_hits = []
    for hit in hits:
        value = str(hit.metadata.get("value") or hit.text).strip()
        normalized_value = _normalize_value(value)
        if is_recallable_value(value) and normalized_value in normalized_question:
            value_hits.append(_value_hit(hit, value, "exact", 1.0))
    return value_hits


def _vector_value_hits(hits: list[VectorSearchHit], threshold: float) -> list[ValueHit]:
    return [
        _value_hit(hit, str(hit.metadata.get("value") or hit.text), "vector", hit.score)
        for hit in hits
        if hit.score >= threshold and is_recallable_value(str(hit.metadata.get("value") or hit.text))
    ]


def _value_hit(hit: VectorSearchHit, value: str, source: str, score: float) -> ValueHit:
    table_name, column_name = _split_value_asset_id(hit.asset_id)
    return ValueHit(
        table_name=str(hit.metadata.get("table_name") or table_name),
        column_name=str(hit.metadata.get("column_name") or column_name),
        matched_value=value,
        source=source,
        score=score,
    )


def _deduplicate_value_hits(hits: list[ValueHit]) -> list[ValueHit]:
    best: dict[tuple[str, str, str], ValueHit] = {}
    for hit in hits:
        key = (hit.table_name, hit.column_name, hit.matched_value)
        if key not in best or hit.score > best[key].score:
            best[key] = hit
    return sorted(best.values(), key=lambda hit: (-hit.score, hit.table_name, hit.column_name, hit.matched_value))


def _split_value_asset_id(asset_id: str) -> tuple[str, str]:
    stripped_asset_id = _strip_datasource_prefix(asset_id)
    qualified_column, _, _ = stripped_asset_id.partition(":")
    table_name, _, column_name = qualified_column.partition(".")
    return table_name, column_name


def _strip_datasource_prefix(asset_id: str) -> str:
    first, separator, rest = asset_id.partition(":")
    if separator and "." not in first:
        return rest
    return asset_id


def _datasource_filter(datasource_name: str) -> dict:
    return {"must": [{"key": "metadata.datasource", "match": {"value": datasource_name}}]}


def _normalize_value(value: str) -> str:
    return "".join(str(value).lower().split())


def is_recallable_value(value: str) -> bool:
    normalized = _normalize_value(value)
    if not normalized:
        return False
    if LOW_INFORMATION_VALUE_PATTERN.fullmatch(normalized):
        return False
    if DATE_LITERAL_PATTERN.fullmatch(normalized):
        return False
    return True
