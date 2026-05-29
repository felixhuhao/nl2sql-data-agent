import json

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from backend.app.core.db import get_duckdb_connection, get_sqlite_engine, sqlite_session
from backend.app.metadata.models import MetaColumn, MetaRelationship, MetaTable, create_metadata_schema
from backend.app.metadata.semantic_overlay import (
    COLUMN_SEMANTICS,
    CONFIRMED_RELATIONSHIPS,
    DIMENSION_COLUMNS,
    METRIC_COLUMNS,
    TABLE_COLUMN_SEMANTICS,
    TABLE_SEMANTICS,
    sample_value_fallbacks_json,
)


def sync_metadata() -> dict[str, int]:
    engine = get_sqlite_engine()
    create_metadata_schema(engine)
    _ensure_relationship_columns(engine)
    with sqlite_session() as session:
        duckdb_tables = _read_duckdb_columns()
        table_count = _sync_tables_and_columns(session, duckdb_tables)
        relationship_count = _sync_relationships(session, duckdb_tables)
        column_count = sum(len(columns) for columns in duckdb_tables.values())
    return {
        "tables": table_count,
        "columns": column_count,
        "relationships": relationship_count,
    }


def _read_duckdb_columns() -> dict[str, list[dict[str, str]]]:
    with get_duckdb_connection(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'main'
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
    tables: dict[str, list[dict[str, str]]] = {}
    for table_name, column_name, data_type in rows:
        tables.setdefault(table_name, []).append(
            {"column_name": column_name, "data_type": data_type}
        )
    return tables


def _sync_tables_and_columns(session: Session, duckdb_tables: dict[str, list[dict[str, str]]]) -> int:
    for table_name, columns in duckdb_tables.items():
        display_name, description, domain = TABLE_SEMANTICS.get(table_name, (table_name, None, None))
        table = session.scalar(select(MetaTable).where(MetaTable.table_name == table_name))
        if table is None:
            table = MetaTable(table_name=table_name)
            session.add(table)
        table.display_name = display_name
        table.description = description
        table.domain = domain
        table.row_count = _count_rows(table_name)
        table.enabled = True
        session.flush()

        actual_column_names = {column["column_name"] for column in columns}
        for stale_column in list(table.columns):
            if stale_column.column_name not in actual_column_names:
                session.delete(stale_column)

        for column in columns:
            meta_column = session.scalar(
                select(MetaColumn).where(
                    MetaColumn.table_id == table.id,
                    MetaColumn.column_name == column["column_name"],
                )
            )
            if meta_column is None:
                meta_column = MetaColumn(table_id=table.id, column_name=column["column_name"])
                session.add(meta_column)
            column_name = column["column_name"]
            meta_column.data_type = column["data_type"]
            meta_column.description = TABLE_COLUMN_SEMANTICS.get(
                (table_name, column_name), COLUMN_SEMANTICS.get(column_name)
            )
            meta_column.is_dimension = column_name in DIMENSION_COLUMNS
            meta_column.is_metric = column_name in METRIC_COLUMNS
            meta_column.sample_values = _profile_sample_values_json(table_name, column_name)
    return len(duckdb_tables)


def _sync_relationships(session: Session, duckdb_tables: dict[str, list[dict[str, str]]]) -> int:
    relationships = _infer_relationships(duckdb_tables)
    for relationship in _overlay_relationships(duckdb_tables).values():
        relationships[(relationship["source_table"], relationship["source_column"], relationship["target_table"], relationship["target_column"])] = relationship

    for relationship in relationships.values():
        existing = session.scalar(
            select(MetaRelationship).where(
                MetaRelationship.source_table == relationship["source_table"],
                MetaRelationship.source_column == relationship["source_column"],
                MetaRelationship.target_table == relationship["target_table"],
                MetaRelationship.target_column == relationship["target_column"],
            )
        )
        if existing is None:
            existing = MetaRelationship(
                source_table=relationship["source_table"],
                source_column=relationship["source_column"],
                target_table=relationship["target_table"],
                target_column=relationship["target_column"],
            )
            session.add(existing)
        existing.relationship_type = relationship["relationship_type"]
        existing.source = relationship["source"]
        existing.confidence = relationship["confidence"]
        existing.fanout_risk = relationship["fanout_risk"]
        existing.description = relationship["description"]

    current_keys = set(relationships)
    all_relationships = session.scalars(select(MetaRelationship)).all()
    for rel in all_relationships:
        if (rel.source_table, rel.source_column, rel.target_table, rel.target_column) not in current_keys:
            session.delete(rel)
    return len(relationships)


def _overlay_relationships(duckdb_tables: dict[str, list[dict[str, str]]]) -> dict[tuple[str, str, str, str], dict]:
    relationships = {}
    for source_table, source_column, target_table, target_column, relationship_type, description in CONFIRMED_RELATIONSHIPS:
        if not _relationship_exists_in_schema(
            duckdb_tables,
            source_table,
            source_column,
            target_table,
            target_column,
        ):
            continue
        relationships[(source_table, source_column, target_table, target_column)] = {
            "source_table": source_table,
            "source_column": source_column,
            "target_table": target_table,
            "target_column": target_column,
            "relationship_type": relationship_type,
            "source": "overlay",
            "confidence": 1.0,
            "fanout_risk": _fanout_risk(source_table, target_table, relationship_type),
            "description": description,
        }
    return relationships


def _relationship_exists_in_schema(
    duckdb_tables: dict[str, list[dict[str, str]]],
    source_table: str,
    source_column: str,
    target_table: str,
    target_column: str,
) -> bool:
    table_columns = {
        table_name: {column["column_name"] for column in columns}
        for table_name, columns in duckdb_tables.items()
    }
    return (
        source_column in table_columns.get(source_table, set())
        and target_column in table_columns.get(target_table, set())
    )


def _infer_relationships(duckdb_tables: dict[str, list[dict[str, str]]]) -> dict[tuple[str, str, str, str], dict]:
    table_columns = {
        table_name: {column["column_name"] for column in columns}
        for table_name, columns in duckdb_tables.items()
    }
    key_to_dimension_table = {
        column_name: table_name
        for table_name, columns in table_columns.items()
        if table_name.startswith("dim_")
        for column_name in columns
        if column_name.endswith("_key")
    }
    relationships: dict[tuple[str, str, str, str], dict] = {}

    for source_table, columns in table_columns.items():
        if not source_table.startswith("fact_"):
            continue
        for source_column in columns:
            if not source_column.endswith("_key"):
                continue
            target_table = key_to_dimension_table.get(source_column)
            if target_table and target_table != source_table:
                _add_inferred_relationship(
                    relationships,
                    source_table,
                    source_column,
                    target_table,
                    source_column,
                    0.9,
                    f"按同名 key 推断 {source_table}.{source_column} -> {target_table}.{source_column}",
                )

        if "order_id" in columns and "fact_orders" in table_columns and source_table != "fact_orders":
            _add_inferred_relationship(
                relationships,
                source_table,
                "order_id",
                "fact_orders",
                "order_id",
                0.8,
                f"按订单 ID 推断 {source_table}.order_id -> fact_orders.order_id",
            )

    return relationships


def _add_inferred_relationship(
    relationships: dict[tuple[str, str, str, str], dict],
    source_table: str,
    source_column: str,
    target_table: str,
    target_column: str,
    confidence: float,
    description: str,
) -> None:
    relationships[(source_table, source_column, target_table, target_column)] = {
        "source_table": source_table,
        "source_column": source_column,
        "target_table": target_table,
        "target_column": target_column,
        "relationship_type": "many_to_one",
        "source": "inferred",
        "confidence": confidence,
        "fanout_risk": _fanout_risk(source_table, target_table, "many_to_one"),
        "description": description,
    }


def _count_rows(table_name: str) -> int:
    with get_duckdb_connection(read_only=True) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}").fetchone()[0]


def _profile_sample_values_json(table_name: str, column_name: str, limit: int = 5) -> str | None:
    with get_duckdb_connection(read_only=True) as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT {_quote_identifier(column_name)}
            FROM {_quote_identifier(table_name)}
            WHERE {_quote_identifier(column_name)} IS NOT NULL
            ORDER BY 1
            LIMIT {limit}
            """
        ).fetchall()
    values = [row[0] for row in rows]
    if not values:
        return sample_value_fallbacks_json(column_name)
    return json.dumps(values, ensure_ascii=False, default=str)


def _fanout_risk(source_table: str, target_table: str, relationship_type: str) -> str:
    if relationship_type == "one_to_many":
        return "high"
    if source_table == "fact_order_items" and target_table == "fact_orders":
        return "medium"
    return "low"


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _ensure_relationship_columns(engine) -> None:
    existing_columns = {column["name"] for column in inspect(engine).get_columns("meta_relationships")}
    columns_to_add = {
        "source": "TEXT DEFAULT 'inferred'",
        "confidence": "REAL DEFAULT 0.8",
        "fanout_risk": "TEXT DEFAULT 'low'",
    }
    with engine.begin() as connection:
        for column_name, ddl in columns_to_add.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE meta_relationships ADD COLUMN {column_name} {ddl}"))
