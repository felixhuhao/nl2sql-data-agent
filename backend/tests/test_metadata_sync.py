from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.metadata import sync
from backend.app.connectors.schema import ColumnMeta, SchemaSnapshot, TableMeta
from backend.app.metadata.models import MetaColumn, MetaRelationship, MetaTable, create_metadata_schema
from backend.app.metadata.seed import seed_semantics


def test_sync_metadata_uses_connector_snapshot(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    snapshot = _snapshot(
        [
            TableMeta(
                name="fact_orders",
                row_count=12,
                columns=[ColumnMeta(name="payment_amount", data_type="Decimal(12,2)", nullable=False)],
                engine="MergeTree",
            )
        ],
        datasource_name="clickhouse_ecommerce",
    )
    connector = FakeConnector(snapshot)
    monkeypatch.setattr(sync, "get_sqlite_engine", lambda: engine)
    monkeypatch.setattr(sync, "sqlite_session", lambda: _session_scope(engine))
    monkeypatch.setattr(sync, "get_datasource_manager", lambda: FakeManager(connector))

    result = sync.sync_metadata(datasource_name="clickhouse_ecommerce")

    with Session(engine) as session:
        table = session.scalar(select(MetaTable).where(MetaTable.datasource == "clickhouse_ecommerce"))
        column = session.scalar(select(MetaColumn).where(MetaColumn.datasource == "clickhouse_ecommerce"))
    assert connector.synced is True
    assert result == {"tables": 1, "columns": 1, "relationships": 0}
    assert table.table_name == "fact_orders"
    assert table.engine == "MergeTree"
    assert column.column_name == "payment_amount"


def test_sync_physical_metadata_then_seed_semantics():
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    with Session(engine) as session:
        sync._sync_tables_and_columns(
            session,
            _snapshot(
                [
                    TableMeta(
                        name="fact_orders",
                        row_count=12,
                        columns=[
                            ColumnMeta(name="payment_amount", data_type="DECIMAL", nullable=False),
                            ColumnMeta(name="order_id", data_type="VARCHAR", nullable=False),
                        ],
                    )
                ]
            ),
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


def test_sync_does_not_override_existing_semantics():
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    snapshot = _snapshot(
        [
            TableMeta(
                name="fact_orders",
                row_count=12,
                columns=[ColumnMeta(name="payment_amount", data_type="DECIMAL", nullable=False)],
            )
        ]
    )
    with Session(engine) as session:
        sync._sync_tables_and_columns(session, snapshot)
        seed_semantics(session)
        table = session.scalar(select(MetaTable).where(MetaTable.table_name == "fact_orders"))
        column = session.scalar(select(MetaColumn).where(MetaColumn.column_name == "payment_amount"))
        table.description = "人工表描述"
        column.description = "人工字段描述"

        sync._sync_tables_and_columns(session, snapshot)
        seed_semantics(session)
        session.commit()

        assert table.description == "人工表描述"
        assert column.description == "人工字段描述"


def test_sync_tables_and_columns_persists_olap_metadata():
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    snapshot = _snapshot(
        [
            TableMeta(
                name="fact_orders",
                row_count=12,
                columns=[
                    ColumnMeta(
                        name="order_status",
                        data_type="String",
                        nullable=True,
                        sample_values=["paid"],
                        is_partition_key=True,
                        is_sorting_key=True,
                        is_primary_key=True,
                        low_cardinality=True,
                    )
                ],
                engine="MergeTree",
                partition_key="intDiv(date_key, 100)",
                sorting_key="date_key, order_id",
            )
        ],
        datasource_name="clickhouse_ecommerce",
    )
    with Session(engine) as session:
        sync._sync_tables_and_columns(session, snapshot, datasource_name="clickhouse_ecommerce")
        session.commit()

        table = session.scalar(select(MetaTable).where(MetaTable.table_name == "fact_orders"))
        column = session.scalar(select(MetaColumn).where(MetaColumn.column_name == "order_status"))

        assert table.engine == "MergeTree"
        assert table.partition_key == "intDiv(date_key, 100)"
        assert table.sorting_key == "date_key, order_id"
        assert column.nullable is True
        assert column.sample_values == '["paid"]'
        assert column.is_partition_key is True
        assert column.is_sorting_key is True
        assert column.is_primary_key is True
        assert column.low_cardinality is True


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


def _snapshot(tables: list[TableMeta], datasource_name: str = "duckdb_ecommerce") -> SchemaSnapshot:
    return SchemaSnapshot(
        tables=tables,
        datasource_name=datasource_name,
        synced_at=datetime.now(timezone.utc),
    )


@contextmanager
def _session_scope(engine):
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


class FakeConnector:
    def __init__(self, snapshot: SchemaSnapshot) -> None:
        self.snapshot = snapshot
        self.synced = False

    def sync_schema(self) -> SchemaSnapshot:
        self.synced = True
        return self.snapshot


class FakeManager:
    def __init__(self, connector: FakeConnector) -> None:
        self.connector = connector

    def get(self, datasource_name: str) -> FakeConnector:
        assert datasource_name == self.connector.snapshot.datasource_name
        return self.connector
