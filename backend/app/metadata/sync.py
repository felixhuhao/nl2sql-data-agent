import json

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from backend.app.core.db import get_duckdb_connection, get_sqlite_engine, sqlite_session
from backend.app.connectors.registry import get_datasource_manager
from backend.app.connectors.schema import SchemaSnapshot
from backend.app.metadata.models import DEFAULT_DATASOURCE, MetaColumn, MetaRelationship, MetaTable, create_metadata_schema
from backend.app.metadata.seed import seed_semantics


def sync_metadata(datasource_name: str = DEFAULT_DATASOURCE) -> dict[str, int]:
    engine = get_sqlite_engine()
    create_metadata_schema(engine)
    _ensure_relationship_columns(engine)
    connector = get_datasource_manager().get(datasource_name)
    snapshot = connector.sync_schema()
    synced_datasource = snapshot.datasource_name
    schema_tables = _relationship_input_from_snapshot(snapshot)
    with sqlite_session() as session:
        table_count = _sync_tables_and_columns(session, snapshot, datasource_name=synced_datasource)
        seed_semantics(session, datasource_name=synced_datasource)
        relationship_count = _sync_relationships(session, schema_tables, datasource_name=synced_datasource)
        column_count = sum(len(table.columns) for table in snapshot.tables)
    return {
        "tables": table_count,
        "columns": column_count,
        "relationships": relationship_count,
    }


# Legacy DuckDB helpers are kept for older direct callers. Main sync flow should use connector.sync_schema().
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


def _sync_tables_and_columns(
    session: Session,
    snapshot: SchemaSnapshot,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> int:
    for table_snapshot in snapshot.tables:
        table_name = table_snapshot.name
        table = session.scalar(
            select(MetaTable).where(MetaTable.datasource == datasource_name, MetaTable.table_name == table_name)
        )
        if table is None:
            table = MetaTable(datasource=datasource_name, table_name=table_name)
            session.add(table)
        table.row_count = table_snapshot.row_count
        table.enabled = True
        table.engine = table_snapshot.engine
        table.partition_key = table_snapshot.partition_key
        table.sorting_key = table_snapshot.sorting_key
        session.flush()

        actual_column_names = {column.name for column in table_snapshot.columns}
        for stale_column in list(table.columns):
            if stale_column.column_name not in actual_column_names:
                session.delete(stale_column)

        for column in table_snapshot.columns:
            meta_column = session.scalar(
                select(MetaColumn).where(
                    MetaColumn.table_id == table.id,
                    MetaColumn.column_name == column.name,
                )
            )
            if meta_column is None:
                meta_column = MetaColumn(
                    datasource=datasource_name,
                    table_id=table.id,
                    column_name=column.name,
                )
                session.add(meta_column)
            # Keep the denormalized datasource aligned with the owning table for fast scoped queries.
            meta_column.datasource = datasource_name
            meta_column.data_type = column.data_type
            meta_column.nullable = column.nullable
            meta_column.sample_values = _sample_values_json(column.sample_values)
            meta_column.is_partition_key = column.is_partition_key
            meta_column.is_sorting_key = column.is_sorting_key
            meta_column.is_primary_key = column.is_primary_key
            meta_column.low_cardinality = column.low_cardinality
    return len(snapshot.tables)


def _relationship_input_from_snapshot(snapshot: SchemaSnapshot) -> dict[str, list[dict[str, str]]]:
    return {
        table.name: [
            {
                "column_name": column.name,
                "data_type": column.data_type,
            }
            for column in table.columns
        ]
        for table in snapshot.tables
    }


def _sync_relationships(
    session: Session,
    duckdb_tables: dict[str, list[dict[str, str]]],
    datasource_name: str = DEFAULT_DATASOURCE,
) -> int:
    relationships = _infer_relationships(duckdb_tables)
    for relationship in _overlay_relationships_from_db(session, duckdb_tables, datasource_name=datasource_name).values():
        relationships[
            (
                relationship["source_table"],
                relationship["source_column"],
                relationship["target_table"],
                relationship["target_column"],
            )
        ] = relationship

    for relationship in relationships.values():
        existing = session.scalar(
            select(MetaRelationship).where(
                MetaRelationship.source_table == relationship["source_table"],
                MetaRelationship.source_column == relationship["source_column"],
                MetaRelationship.target_table == relationship["target_table"],
                MetaRelationship.target_column == relationship["target_column"],
                MetaRelationship.datasource == datasource_name,
            )
        )
        if existing is None:
            existing = MetaRelationship(
                datasource=datasource_name,
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
    all_relationships = session.scalars(
        select(MetaRelationship).where(MetaRelationship.datasource == datasource_name)
    ).all()
    for rel in all_relationships:
        if (rel.source_table, rel.source_column, rel.target_table, rel.target_column) not in current_keys:
            session.delete(rel)
    return len(relationships)


def _overlay_relationships_from_db(
    session: Session,
    duckdb_tables: dict[str, list[dict[str, str]]],
    datasource_name: str = DEFAULT_DATASOURCE,
) -> dict[tuple[str, str, str, str], dict]:
    relationships = {}
    overlay_relationships = session.scalars(
        select(MetaRelationship).where(
            MetaRelationship.datasource == datasource_name,
            MetaRelationship.source == "overlay",
        )
    ).all()
    for relationship in overlay_relationships:
        if not _relationship_exists_in_schema(
            duckdb_tables,
            relationship.source_table,
            relationship.source_column,
            relationship.target_table,
            relationship.target_column,
        ):
            continue
        relationships[
            (
                relationship.source_table,
                relationship.source_column,
                relationship.target_table,
                relationship.target_column,
            )
        ] = {
            "source_table": relationship.source_table,
            "source_column": relationship.source_column,
            "target_table": relationship.target_table,
            "target_column": relationship.target_column,
            "relationship_type": relationship.relationship_type,
            "source": relationship.source,
            "confidence": relationship.confidence,
            "fanout_risk": relationship.fanout_risk,
            "description": relationship.description,
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
    return json.dumps(values, ensure_ascii=False, default=str) if values else None


def _sample_values_json(values: list[str]) -> str | None:
    return json.dumps(values, ensure_ascii=False, default=str) if values else None


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
