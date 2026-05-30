from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.metadata import sync
from backend.app.metadata.models import MetaColumn, MetaRelationship, MetaTable, create_metadata_schema
from backend.app.metadata.seed import seed_semantics


def test_sync_physical_metadata_then_seed_semantics(monkeypatch):
    monkeypatch.setattr(sync, "_count_rows", lambda table_name: 12)
    monkeypatch.setattr(sync, "_profile_sample_values_json", lambda table_name, column_name: None)

    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    with Session(engine) as session:
        sync._sync_tables_and_columns(
            session,
            {
                "fact_orders": [
                    {"column_name": "payment_amount", "data_type": "DECIMAL"},
                    {"column_name": "order_id", "data_type": "VARCHAR"},
                ]
            },
        )
        seed_semantics(session)
        session.commit()

        table = session.scalar(select(MetaTable).where(MetaTable.table_name == "fact_orders"))
        column = session.scalar(select(MetaColumn).where(MetaColumn.column_name == "payment_amount"))
        extra_demo_table = session.scalar(select(MetaTable).where(MetaTable.table_name == "dim_products"))

        assert table.display_name == "订单事实表"
        assert table.description == "订单支付金额、折扣和状态"
        assert table.row_count == 12
        assert column.description == "订单实付金额，销售额口径字段"
        assert column.is_metric is True
        assert extra_demo_table is None


def test_sync_does_not_override_existing_semantics(monkeypatch):
    monkeypatch.setattr(sync, "_count_rows", lambda table_name: 12)
    monkeypatch.setattr(sync, "_profile_sample_values_json", lambda table_name, column_name: None)

    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    duckdb_tables = {
        "fact_orders": [
            {"column_name": "payment_amount", "data_type": "DECIMAL"},
        ]
    }
    with Session(engine) as session:
        sync._sync_tables_and_columns(session, duckdb_tables)
        seed_semantics(session)
        table = session.scalar(select(MetaTable).where(MetaTable.table_name == "fact_orders"))
        column = session.scalar(select(MetaColumn).where(MetaColumn.column_name == "payment_amount"))
        table.description = "人工表描述"
        column.description = "人工字段描述"

        sync._sync_tables_and_columns(session, duckdb_tables)
        seed_semantics(session)
        session.commit()

        assert table.description == "人工表描述"
        assert column.description == "人工字段描述"


def test_sync_relationships_reads_overlay_from_db():
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    duckdb_tables = {
        "fact_orders": [
            {"column_name": "region_key", "data_type": "INTEGER"},
        ],
        "dim_regions": [
            {"column_name": "region_key", "data_type": "INTEGER"},
        ],
    }
    with Session(engine) as session:
        session.add(
            MetaRelationship(
                source_table="fact_orders",
                source_column="region_key",
                target_table="dim_regions",
                target_column="region_key",
                relationship_type="many_to_one",
                source="overlay",
                confidence=1.0,
                fanout_risk="low",
                description="人工确认区域关系",
            )
        )

        relationship_count = sync._sync_relationships(session, duckdb_tables)
        session.commit()

        relationship = session.scalar(
            select(MetaRelationship).where(
                MetaRelationship.source_table == "fact_orders",
                MetaRelationship.source_column == "region_key",
                MetaRelationship.target_table == "dim_regions",
                MetaRelationship.target_column == "region_key",
            )
        )
        assert relationship_count == 1
        assert relationship.source == "overlay"
        assert relationship.confidence == 1.0
        assert relationship.description == "人工确认区域关系"
