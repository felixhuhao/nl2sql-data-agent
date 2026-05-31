from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.metadata.models import (
    MetaColumn,
    MetaColumnAlias,
    MetaMetric,
    MetaTable,
    MetaVerifiedQuery,
    create_metadata_schema,
)
from backend.app.metadata.vector.embedding import embed_texts, get_embedding_dimension
from backend.app.metadata.vector.store import (
    LanceVectorStore,
    VectorIndexMetadata,
    VectorRow,
    get_vector_store,
)


DEFAULT_EMBED_BATCH_SIZE = 64


@dataclass(frozen=True)
class VectorIndexBuildResult:
    embedding_model: str
    embedding_dimension: int
    built_at: str
    asset_counts: dict[str, int]


@dataclass(frozen=True)
class VectorAsset:
    table_name: str
    asset_type: str
    asset_id: str
    text: str
    metadata: dict[str, Any]


def rebuild_vector_index(
    *,
    vector_store: LanceVectorStore | None = None,
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
) -> VectorIndexBuildResult:
    settings = get_settings()
    if not settings.vector_enabled:
        raise RuntimeError("VECTOR_ENABLED must be true to rebuild the vector index.")
    if not settings.embedding_model:
        raise RuntimeError("EMBEDDING_MODEL is required to rebuild the vector index.")

    create_metadata_schema(get_sqlite_engine())
    vector_store = vector_store or get_vector_store()
    embedding_dimension = get_embedding_dimension()
    vector_store.ensure_tables(embedding_dimension)
    vector_store.clear_vector_tables()

    with sqlite_session() as session:
        assets = build_vector_assets(session)

    rows: list[VectorRow] = []
    for batch in _batched(assets, batch_size):
        vectors = embed_texts([asset.text for asset in batch])
        for asset, vector in zip(batch, vectors, strict=True):
            if vector is None:
                continue
            rows.append(
                VectorRow(
                    table_name=asset.table_name,
                    asset_type=asset.asset_type,
                    asset_id=asset.asset_id,
                    text=asset.text,
                    vector=vector,
                    metadata=asset.metadata,
                )
            )

    if rows:
        vector_store.upsert_rows(rows)

    built_at = datetime.now(UTC).isoformat()
    asset_counts = dict(Counter(row.asset_type for row in rows))
    metadata = VectorIndexMetadata(
        embedding_model=settings.embedding_model,
        embedding_dimension=embedding_dimension,
        built_at=built_at,
        asset_counts=asset_counts,
    )
    vector_store.write_metadata(metadata)
    return VectorIndexBuildResult(
        embedding_model=settings.embedding_model,
        embedding_dimension=embedding_dimension,
        built_at=built_at,
        asset_counts=asset_counts,
    )


def build_vector_assets(session: Session) -> list[VectorAsset]:
    aliases = _aliases_by_column(session)
    assets: list[VectorAsset] = []
    assets.extend(_table_assets(session))
    assets.extend(_column_assets(session, aliases))
    assets.extend(_metric_assets(session))
    assets.extend(_verified_query_assets(session))
    assets.extend(_value_assets(session))
    return [asset for asset in assets if asset.text.strip()]


def _table_assets(session: Session) -> list[VectorAsset]:
    tables = session.scalars(
        select(MetaTable)
        .where(MetaTable.enabled.is_(True))
        .order_by(MetaTable.table_name)
    ).all()
    return [
        VectorAsset(
            table_name="table_vectors",
            asset_type="table",
            asset_id=table.table_name,
            text=_join_text(table.table_name, table.display_name, table.description, table.domain),
            metadata={
                "table_name": table.table_name,
                "display_name": table.display_name,
                "description": table.description,
                "domain": table.domain,
                "row_count": table.row_count,
            },
        )
        for table in tables
    ]


def _column_assets(
    session: Session,
    aliases: dict[tuple[str, str], list[str]],
) -> list[VectorAsset]:
    columns = session.scalars(
        select(MetaColumn)
        .join(MetaTable)
        .where(MetaTable.enabled.is_(True))
        .order_by(MetaTable.table_name, MetaColumn.id)
    ).all()
    assets: list[VectorAsset] = []
    for column in columns:
        table_name = column.table.table_name
        alias_values = aliases.get((table_name, column.column_name), [])
        sample_values = _parse_json_list(column.sample_values)
        assets.append(
            VectorAsset(
                table_name="column_vectors",
                asset_type="column",
                asset_id=f"{table_name}.{column.column_name}",
                text=_join_text(
                    table_name,
                    column.column_name,
                    column.description,
                    column.data_type,
                    *alias_values,
                    *[str(value) for value in sample_values],
                ),
                metadata={
                    "table_name": table_name,
                    "column_name": column.column_name,
                    "data_type": column.data_type,
                    "description": column.description,
                    "is_dimension": column.is_dimension,
                    "is_metric": column.is_metric,
                    "aliases": alias_values,
                    "sample_values": sample_values,
                },
            )
        )
    return assets


def _metric_assets(session: Session) -> list[VectorAsset]:
    metrics = session.scalars(
        select(MetaMetric)
        .where(MetaMetric.enabled.is_(True))
        .order_by(MetaMetric.name)
    ).all()
    return [
        VectorAsset(
            table_name="metric_vectors",
            asset_type="metric",
            asset_id=metric.name,
            text=_join_text(
                metric.name,
                metric.label,
                metric.description,
                metric.expression,
                metric.default_time_column,
                *_parse_json_list(metric.allowed_dimensions),
            ),
            metadata={
                "name": metric.name,
                "label": metric.label,
                "expression": metric.expression,
                "description": metric.description,
                "default_time_column": metric.default_time_column,
                "allowed_dimensions": _parse_json_list(metric.allowed_dimensions),
            },
        )
        for metric in metrics
    ]


def _verified_query_assets(session: Session) -> list[VectorAsset]:
    queries = session.scalars(
        select(MetaVerifiedQuery)
        .where(MetaVerifiedQuery.enabled.is_(True))
        .order_by(MetaVerifiedQuery.query_id)
    ).all()
    return [
        VectorAsset(
            table_name="verified_query_vectors",
            asset_type="verified_query",
            asset_id=query.query_id,
            text=_join_text(query.query_id, query.question, *_parse_json_list(query.tags)),
            metadata={
                "query_id": query.query_id,
                "question": query.question,
                "sql": query.sql,
                "tags": _parse_json_list(query.tags),
                "verified_by": query.verified_by,
            },
        )
        for query in queries
    ]


def _value_assets(session: Session) -> list[VectorAsset]:
    columns = session.scalars(
        select(MetaColumn)
        .join(MetaTable)
        .where(MetaTable.enabled.is_(True), MetaColumn.sample_values.is_not(None))
        .order_by(MetaTable.table_name, MetaColumn.id)
    ).all()
    assets: list[VectorAsset] = []
    for column in columns:
        table_name = column.table.table_name
        for value in _parse_json_list(column.sample_values):
            text = str(value).strip()
            if not text:
                continue
            assets.append(
                VectorAsset(
                    table_name="value_vectors",
                    asset_type="value",
                    asset_id=f"{table_name}.{column.column_name}:{text}",
                    text=text,
                    metadata={
                        "table_name": table_name,
                        "column_name": column.column_name,
                        "value": text,
                    },
                )
            )
    return assets


def _aliases_by_column(session: Session) -> dict[tuple[str, str], list[str]]:
    aliases = session.scalars(
        select(MetaColumnAlias).order_by(
            MetaColumnAlias.table_name,
            MetaColumnAlias.column_name,
            MetaColumnAlias.alias,
        )
    ).all()
    result: dict[tuple[str, str], list[str]] = {}
    for alias in aliases:
        result.setdefault((alias.table_name, alias.column_name), []).append(alias.alias)
    return result


def _batched(items: list[VectorAsset], batch_size: int) -> Iterable[list[VectorAsset]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    for index in range(0, len(items), batch_size):
        yield items[index : index + batch_size]


def _join_text(*parts: Any) -> str:
    return " ".join(str(part).strip() for part in parts if part is not None and str(part).strip())


def _parse_json_list(value: str | None) -> list:
    if value is None:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
