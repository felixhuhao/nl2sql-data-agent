from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
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

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.row_id,
            "asset_type": self.asset_type,
            "asset_id": self.asset_id,
            "text": self.text,
            "metadata_json": json.dumps(self.metadata, ensure_ascii=False, sort_keys=True),
            "vector": self.vector,
        }


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

    def to_record(self) -> dict[str, Any]:
        return {
            "id": "current",
            "schema_version": self.schema_version,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "built_at": self.built_at,
            "asset_counts_json": json.dumps(self.asset_counts, ensure_ascii=False, sort_keys=True),
        }


@dataclass(frozen=True)
class VectorIndexStatus:
    status: str
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    built_at: str | None = None
    asset_counts: dict[str, int] = field(default_factory=dict)
    stale_reason: str | None = None


class LanceVectorStore:
    def __init__(self, db_path: Path | str | None = None, db=None) -> None:
        settings = get_settings()
        self.db_path = Path(db_path) if db_path is not None else settings.resolved_vector_db_path()
        self._db = db

    def ensure_tables(self, embedding_dimension: int) -> None:
        existing_tables = set(self._database().table_names())
        vector_schema = _vector_schema(embedding_dimension)
        for table_name in VECTOR_TABLE_NAMES:
            if table_name not in existing_tables:
                self._database().create_table(table_name, schema=vector_schema, exist_ok=True)
        if METADATA_TABLE_NAME not in existing_tables:
            self._database().create_table(METADATA_TABLE_NAME, schema=_metadata_schema(), exist_ok=True)

    def upsert_rows(self, rows: list[VectorRow]) -> None:
        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            _validate_table_name(row.table_name)
            rows_by_table.setdefault(row.table_name, []).append(row.to_record())

        for table_name, records in rows_by_table.items():
            table = self._database().open_table(table_name)
            data = _records_to_arrow_table(records)
            (
                table.merge_insert("id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(data)
            )

    def clear_vector_tables(self) -> None:
        for table_name in VECTOR_TABLE_NAMES:
            self._database().open_table(table_name).delete("id IS NOT NULL")

    def delete_by_ids(self, table_name: str, row_ids: list[str]) -> None:
        _validate_table_name(table_name)
        if not row_ids:
            return
        id_list = ", ".join(_sql_string_literal(row_id) for row_id in row_ids)
        self._database().open_table(table_name).delete(f"id IN ({id_list})")

    def search(
        self,
        table_name: str,
        vector: list[float],
        *,
        limit: int = 10,
        where: str | None = None,
    ) -> list[VectorSearchHit]:
        _validate_table_name(table_name)
        query = self._database().open_table(table_name).search(vector)
        if where:
            query = query.where(where)
        records = query.limit(limit).to_list()
        return [_hit_from_record(record) for record in records]

    def write_metadata(self, metadata: VectorIndexMetadata) -> None:
        table = self._database().open_table(METADATA_TABLE_NAME)
        data = _records_to_arrow_table([metadata.to_record()])
        (
            table.merge_insert("id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(data)
        )

    def read_metadata(self) -> VectorIndexMetadata | None:
        if METADATA_TABLE_NAME not in set(self._database().table_names()):
            return None
        records = (
            self._database()
            .open_table(METADATA_TABLE_NAME)
            .search()
            .where("id = 'current'")
            .limit(1)
            .to_list()
        )
        if not records:
            return None
        return _metadata_from_record(records[0])

    def status(
        self,
        *,
        expected_model: str | None = None,
        expected_dimension: int | None = None,
    ) -> VectorIndexStatus:
        existing_tables = set(self._database().table_names())
        missing_tables = [table_name for table_name in ALL_TABLE_NAMES if table_name not in existing_tables]
        if missing_tables:
            return VectorIndexStatus(
                status="missing",
                stale_reason=f"Missing LanceDB tables: {', '.join(missing_tables)}",
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
        if metadata.schema_version != SCHEMA_VERSION:
            return _stale(status, f"Schema version mismatch: {metadata.schema_version} != {SCHEMA_VERSION}.")
        if expected_model is not None and metadata.embedding_model != expected_model:
            return _stale(status, "Embedding model mismatch.")
        if expected_dimension is not None and metadata.embedding_dimension != expected_dimension:
            return _stale(status, "Embedding dimension mismatch.")
        return status

    def _database(self):
        if self._db is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = _load_lancedb().connect(str(self.db_path))
        return self._db


def get_vector_store() -> LanceVectorStore:
    return LanceVectorStore()


def _stale(status: VectorIndexStatus, reason: str) -> VectorIndexStatus:
    return VectorIndexStatus(
        status="stale",
        embedding_model=status.embedding_model,
        embedding_dimension=status.embedding_dimension,
        built_at=status.built_at,
        asset_counts=status.asset_counts,
        stale_reason=reason,
    )


def _hit_from_record(record: dict[str, Any]) -> VectorSearchHit:
    distance = float(record.get("_distance", 0.0))
    return VectorSearchHit(
        asset_type=str(record["asset_type"]),
        asset_id=str(record["asset_id"]),
        text=str(record.get("text") or ""),
        distance=distance,
        score=1.0 / (1.0 + max(distance, 0.0)),
        metadata=_parse_json_object(record.get("metadata_json")),
    )


def _metadata_from_record(record: dict[str, Any]) -> VectorIndexMetadata:
    return VectorIndexMetadata(
        schema_version=int(record["schema_version"]),
        embedding_model=str(record["embedding_model"]),
        embedding_dimension=int(record["embedding_dimension"]),
        built_at=str(record["built_at"]),
        asset_counts={
            str(key): int(value)
            for key, value in _parse_json_object(record.get("asset_counts_json")).items()
        },
    )


def _parse_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _records_to_arrow_table(records: list[dict[str, Any]]):
    pyarrow = _load_pyarrow()
    return pyarrow.Table.from_pylist(records)


def _vector_schema(embedding_dimension: int):
    pyarrow = _load_pyarrow()
    return pyarrow.schema(
        [
            pyarrow.field("id", pyarrow.string()),
            pyarrow.field("asset_type", pyarrow.string()),
            pyarrow.field("asset_id", pyarrow.string()),
            pyarrow.field("text", pyarrow.string()),
            pyarrow.field("metadata_json", pyarrow.string()),
            pyarrow.field("vector", pyarrow.list_(pyarrow.float32(), embedding_dimension)),
        ]
    )


def _metadata_schema():
    pyarrow = _load_pyarrow()
    return pyarrow.schema(
        [
            pyarrow.field("id", pyarrow.string()),
            pyarrow.field("schema_version", pyarrow.int32()),
            pyarrow.field("embedding_model", pyarrow.string()),
            pyarrow.field("embedding_dimension", pyarrow.int32()),
            pyarrow.field("built_at", pyarrow.string()),
            pyarrow.field("asset_counts_json", pyarrow.string()),
        ]
    )


def _validate_table_name(table_name: str) -> None:
    if table_name not in VECTOR_TABLE_NAMES:
        raise ValueError(f"Unknown vector table: {table_name}")


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _load_lancedb():
    try:
        import lancedb
    except ImportError as exc:
        raise RuntimeError("lancedb is required for vector storage.") from exc
    return lancedb


def _load_pyarrow():
    try:
        import pyarrow
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for LanceDB vector storage.") from exc
    return pyarrow
