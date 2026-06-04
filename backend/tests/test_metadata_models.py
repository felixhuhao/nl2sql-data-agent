from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from backend.app.metadata.models import (
    DEFAULT_DATASOURCE,
    MetaAnalysisSpace,
    MetaMetric,
    MetaTable,
    create_metadata_schema,
)


def test_create_metadata_schema_includes_phase2_semantic_tables():
    engine = create_engine("sqlite:///:memory:")

    create_metadata_schema(engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "meta_metrics",
        "meta_column_aliases",
        "meta_verified_queries",
        "meta_analysis_spaces",
    }.issubset(table_names)


def test_create_metadata_schema_migrates_legacy_datasource_schema_with_indexes():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE meta_tables (
                    id INTEGER PRIMARY KEY,
                    table_name VARCHAR NOT NULL,
                    display_name VARCHAR,
                    description TEXT,
                    domain VARCHAR,
                    row_count INTEGER,
                    enabled BOOLEAN
                )
                """
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX ix_meta_tables_table_name ON meta_tables (table_name)"))
        connection.execute(
            text(
                """
                CREATE TABLE meta_metrics (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    label VARCHAR NOT NULL,
                    expression TEXT NOT NULL,
                    description TEXT,
                    default_time_column VARCHAR,
                    allowed_dimensions TEXT,
                    enabled BOOLEAN
                )
                """
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX ix_meta_metrics_name ON meta_metrics (name)"))
        connection.execute(
            text(
                """
                CREATE TABLE meta_analysis_spaces (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    datasource VARCHAR NOT NULL,
                    tables TEXT NOT NULL,
                    enabled_metrics TEXT NOT NULL,
                    allowed_operations TEXT NOT NULL,
                    enabled BOOLEAN
                )
                """
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX ix_meta_analysis_spaces_name ON meta_analysis_spaces (name)"))
        connection.execute(
            text(
                """
                INSERT INTO meta_tables (id, table_name, display_name, row_count, enabled)
                VALUES (1, 'fact_orders', '订单事实表', 12, 1)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO meta_metrics (id, name, label, expression, enabled)
                VALUES (1, 'sales_amount', '销售额', 'SUM(fact_orders.payment_amount)', 1)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO meta_analysis_spaces
                    (id, name, datasource, tables, enabled_metrics, allowed_operations, enabled)
                VALUES
                    (1, 'legacy_space', 'legacy_datasource', '["fact_orders"]', '["sales_amount"]', '["select"]', 1)
                """
            )
        )

    create_metadata_schema(engine)
    create_metadata_schema(engine)

    table_names = set(inspect(engine).get_table_names())
    with Session(engine) as session:
        table = session.scalar(select(MetaTable).where(MetaTable.table_name == "fact_orders"))
        metric = session.scalar(select(MetaMetric).where(MetaMetric.name == "sales_amount"))
        space = session.scalar(select(MetaAnalysisSpace).where(MetaAnalysisSpace.name == "legacy_space"))

    assert not any(table_name.endswith("_legacy") for table_name in table_names)
    assert table.datasource == DEFAULT_DATASOURCE
    assert metric.datasource == DEFAULT_DATASOURCE
    assert space.datasource == "legacy_datasource"
