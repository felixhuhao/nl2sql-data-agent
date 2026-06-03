from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.app.config import get_settings


SCHEMA_VERSION = 1
METADATA_TABLE_NAME = "_index_metadata"
VECTOR_TABLE_NAMES = (
    "table_vectors",
    "column_vectors",
    "metric_vectors",
    "verified_query_vectors",
    "value_vectors",
)
ALL_TABLE_NAMES = (*VECTOR_TABLE_NAMES, METADATA_TABLE_NAME)
METADATA_VECTOR_SIZE = 1
POINT_ID_NAMESPACE = uuid.UUID("8f2954ca-6bf8-4e55-84ee-fd3ab8f7975f")


@dataclass(frozen=True)
class VectorRow:
    table_name: str
    asset_type: str
    asset_id: str
    text: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def row_id(self) -> str:
        return f"{self.asset_type}:{self.asset_id}"


@dataclass(frozen=True)
class VectorSearchHit:
    asset_type: str
    asset_id: str
    text: str
    distance: float
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorIndexMetadata:
    embedding_model: str
    embedding_dimension: int
    built_at: str
    asset_counts: dict[str, int] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    stale_reason: str | None = None


@dataclass(frozen=True)
class VectorIndexStatus:
    status: str
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    built_at: str | None = None
    asset_counts: dict[str, int] = field(default_factory=dict)
    stale_reason: str | None = None


class QdrantVectorStore:
    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        collection_prefix: str | None = None,
        client=None,
    ) -> None:
        settings = get_settings()
        self.url = url or settings.qdrant_url
        self.api_key = api_key if api_key is not None else settings.qdrant_api_key
        self.collection_prefix = (
            collection_prefix if collection_prefix is not None else settings.qdrant_collection_prefix
        )
        self._client = client

    def ensure_tables(self, embedding_dimension: int) -> None:
        existing_collections = self._collection_names()
        for table_name in VECTOR_TABLE_NAMES:
            collection_name = self._collection_name(table_name)
            if collection_name not in existing_collections:
                self._client_instance().create_collection(
                    collection_name=collection_name,
                    vectors_config=_vector_config(embedding_dimension),
                )
        metadata_collection = self._collection_name(METADATA_TABLE_NAME)
        if metadata_collection not in existing_collections:
            self._client_instance().create_collection(
                collection_name=metadata_collection,
                vectors_config=_vector_config(METADATA_VECTOR_SIZE),
            )

    def upsert_rows(self, rows: list[VectorRow]) -> None:
        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            _validate_table_name(row.table_name)
            rows_by_table.setdefault(row.table_name, []).append(_point_from_row(row))

        for table_name, points in rows_by_table.items():
            self._client_instance().upsert(
                collection_name=self._collection_name(table_name),
                points=points,
                wait=True,
            )

    def clear_vector_tables(self) -> None:
        for table_name in VECTOR_TABLE_NAMES:
            client = self._client_instance()
            client.delete(
                collection_name=self._collection_name(table_name),
                points_selector=_points_selector_for_client({"filter": {}}, client),
                wait=True,
            )

    def delete_by_ids(self, table_name: str, row_ids: list[str]) -> None:
        _validate_table_name(table_name)
        if not row_ids:
            return
        client = self._client_instance()
        client.delete(
            collection_name=self._collection_name(table_name),
            points_selector=_points_selector_for_client(
                {"points": [_point_id_for_row(row_id) for row_id in row_ids]},
                client,
            ),
            wait=True,
        )

    def search(
        self,
        table_name: str,
        vector: list[float],
        *,
        limit: int = 10,
        where: str | dict[str, Any] | None = None,
    ) -> list[VectorSearchHit]:
        _validate_table_name(table_name)
        client = self._client_instance()
        points = self._search_points(
            collection_name=self._collection_name(table_name),
            vector=vector,
            limit=limit,
            query_filter=_query_filter_for_client(where, client),
        )
        return [_hit_from_point(point) for point in points]

    def list_values(self, *, limit: int = 10_000) -> list[VectorSearchHit]:
        points, _ = self._client_instance().scroll(
            collection_name=self._collection_name("value_vectors"),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [_hit_from_point(point) for point in points]

    def write_metadata(self, metadata: VectorIndexMetadata) -> None:
        self._client_instance().upsert(
            collection_name=self._collection_name(METADATA_TABLE_NAME),
            points=[
                {
                    "id": _point_id_for_row("metadata:current"),
                    "vector": [0.0],
                    "payload": {
                        "schema_version": metadata.schema_version,
                        "embedding_model": metadata.embedding_model,
                        "embedding_dimension": metadata.embedding_dimension,
                        "built_at": metadata.built_at,
                        "asset_counts": metadata.asset_counts,
                        "stale_reason": metadata.stale_reason,
                    },
                }
            ],
            wait=True,
        )

    def read_metadata(self) -> VectorIndexMetadata | None:
        if self._collection_name(METADATA_TABLE_NAME) not in self._collection_names():
            return None
        points = self._client_instance().retrieve(
            collection_name=self._collection_name(METADATA_TABLE_NAME),
            ids=[_point_id_for_row("metadata:current")],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        return _metadata_from_payload(_payload_from_point(points[0]))

    def status(
        self,
        *,
        expected_model: str | None = None,
        expected_dimension: int | None = None,
    ) -> VectorIndexStatus:
        existing_collections = self._collection_names()
        missing_tables = [
            table_name
            for table_name in ALL_TABLE_NAMES
            if self._collection_name(table_name) not in existing_collections
        ]
        if missing_tables:
            return VectorIndexStatus(
                status="missing",
                stale_reason=f"Missing Qdrant collections: {', '.join(missing_tables)}",
            )

        metadata = self.read_metadata()
        if metadata is None:
            return VectorIndexStatus(status="missing", stale_reason="Missing index metadata.")

        status = VectorIndexStatus(
            status="ready",
            embedding_model=metadata.embedding_model,
            embedding_dimension=metadata.embedding_dimension,
            built_at=metadata.built_at,
            asset_counts=metadata.asset_counts,
        )
        if metadata.stale_reason:
            return _stale(status, metadata.stale_reason)
        if metadata.schema_version != SCHEMA_VERSION:
            return _stale(status, f"Schema version mismatch: {metadata.schema_version} != {SCHEMA_VERSION}.")
        if expected_model is not None and metadata.embedding_model != expected_model:
            return _stale(status, "Embedding model mismatch.")
        if expected_dimension is not None and metadata.embedding_dimension != expected_dimension:
            return _stale(status, "Embedding dimension mismatch.")
        return status

    def mark_stale(self, reason: str) -> None:
        metadata = self.read_metadata()
        if metadata is None:
            # Missing metadata means the index has not been built successfully yet.
            return
        self.write_metadata(
            VectorIndexMetadata(
                embedding_model=metadata.embedding_model,
                embedding_dimension=metadata.embedding_dimension,
                built_at=metadata.built_at,
                asset_counts=metadata.asset_counts,
                schema_version=metadata.schema_version,
                stale_reason=reason,
            )
        )

    def _search_points(
        self,
        *,
        collection_name: str,
        vector: list[float],
        limit: int,
        query_filter: dict[str, Any] | None,
    ):
        client = self._client_instance()
        if hasattr(client, "search"):
            return client.search(
                collection_name=collection_name,
                query_vector=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        result = client.query_points(
            collection_name=collection_name,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return getattr(result, "points", result)

    def _collection_names(self) -> set[str]:
        collections = self._client_instance().get_collections().collections
        return {_collection_name_from_response(collection) for collection in collections}

    def _collection_name(self, table_name: str) -> str:
        if not self.collection_prefix:
            return table_name
        return f"{self.collection_prefix}_{table_name}"

    def _client_instance(self):
        if self._client is None:
            kwargs = {"url": self.url}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            self._client = _load_qdrant_client().QdrantClient(**kwargs)
        return self._client


def get_vector_store() -> QdrantVectorStore:
    return QdrantVectorStore()


def _stale(status: VectorIndexStatus, reason: str) -> VectorIndexStatus:
    return VectorIndexStatus(
        status="stale",
        embedding_model=status.embedding_model,
        embedding_dimension=status.embedding_dimension,
        built_at=status.built_at,
        asset_counts=status.asset_counts,
        stale_reason=reason,
    )


def _point_from_row(row: VectorRow) -> dict[str, Any]:
    return {
        "id": _point_id_for_row(row.row_id),
        "vector": row.vector,
        "payload": {
            "row_id": row.row_id,
            "asset_type": row.asset_type,
            "asset_id": row.asset_id,
            "text": row.text,
            "metadata": row.metadata,
        },
    }


def _hit_from_point(point) -> VectorSearchHit:
    payload = _payload_from_point(point)
    score = _score_from_point(point)
    return VectorSearchHit(
        asset_type=str(payload["asset_type"]),
        asset_id=str(payload["asset_id"]),
        text=str(payload.get("text") or ""),
        # Qdrant cosine search returns similarity as score, so distance is derived for compatibility.
        distance=round(max(0.0, 1.0 - score), 12),
        score=score,
        metadata=_dict_or_empty(payload.get("metadata")),
    )


def _metadata_from_payload(payload: dict[str, Any]) -> VectorIndexMetadata:
    return VectorIndexMetadata(
        schema_version=int(payload["schema_version"]),
        embedding_model=str(payload["embedding_model"]),
        embedding_dimension=int(payload["embedding_dimension"]),
        built_at=str(payload["built_at"]),
        asset_counts={
            str(key): int(value)
            for key, value in _dict_or_empty(payload.get("asset_counts")).items()
        },
        stale_reason=payload.get("stale_reason"),
    )


def _payload_from_point(point) -> dict[str, Any]:
    if isinstance(point, dict):
        return _dict_or_empty(point.get("payload"))
    return _dict_or_empty(getattr(point, "payload", None))


def _score_from_point(point) -> float:
    if isinstance(point, dict):
        return float(point.get("score") or 0.0)
    return float(getattr(point, "score", 0.0) or 0.0)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _point_id_for_row(row_id: str) -> str:
    return str(uuid.uuid5(POINT_ID_NAMESPACE, row_id))


def _vector_config(embedding_dimension: int) -> dict[str, Any]:
    return {"size": embedding_dimension, "distance": "Cosine"}


def _query_filter_for_client(where: str | dict[str, Any] | None, client) -> Any:
    payload = _payload_filter(where)
    if payload is None or not _is_real_qdrant_client(client):
        return payload
    return _qdrant_filter_model(payload)


def _points_selector_for_client(selector: dict[str, Any], client) -> Any:
    if not _is_real_qdrant_client(client):
        return selector

    models = _load_qdrant_client().models
    if "points" in selector:
        return models.PointIdsList(points=selector["points"])
    if "filter" in selector:
        return models.FilterSelector(filter=_qdrant_filter_model(selector["filter"]))
    raise ValueError(f"Unsupported Qdrant points selector: {selector}")


def _qdrant_filter_model(payload: dict[str, Any]):
    models = _load_qdrant_client().models
    must = []
    for condition in payload.get("must", []):
        match = condition.get("match") or {}
        if "value" not in match:
            raise ValueError(f"Unsupported Qdrant filter condition: {condition}")
        must.append(
            models.FieldCondition(
                key=str(condition["key"]),
                match=models.MatchValue(value=match["value"]),
            )
        )
    return models.Filter(must=must or None)


def _is_real_qdrant_client(client) -> bool:
    return client.__class__.__module__.startswith("qdrant_client.")


def _payload_filter(where: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if where is None:
        return None
    if isinstance(where, dict):
        return where
    match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*'((?:[^']|'')*)'\s*", where)
    if match is None:
        raise ValueError(f"Unsupported Qdrant filter expression: {where}")
    field, value = match.groups()
    return {"must": [{"key": field, "match": {"value": value.replace("''", "'")}}]}


def _collection_name_from_response(collection) -> str:
    if isinstance(collection, dict):
        return str(collection["name"])
    return str(collection.name)


def _validate_table_name(table_name: str) -> None:
    if table_name not in VECTOR_TABLE_NAMES:
        raise ValueError(f"Unknown vector table: {table_name}")


def _load_qdrant_client():
    try:
        import qdrant_client
    except ImportError as exc:
        raise RuntimeError("qdrant-client is required for vector storage.") from exc
    return qdrant_client
