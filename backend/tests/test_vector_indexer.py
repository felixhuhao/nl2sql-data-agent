import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.config import DEFAULT_EMBEDDING_MODEL
from backend.app.metadata.models import (
    Base,
    DEFAULT_DATASOURCE,
    MetaColumn,
    MetaColumnAlias,
    MetaMetric,
    MetaTable,
    MetaVerifiedQuery,
)
from backend.app.metadata.vector import indexer


def test_build_vector_assets_creates_all_asset_types():
    session = _session_with_assets()

    assets = indexer.build_vector_assets(session)

    assert {asset.asset_type for asset in assets} == {
        "table",
        "column",
        "metric",
        "verified_query",
        "value",
    }
    assert _asset(assets, "table", _asset_id("fact_orders")).text == "fact_orders 订单事实表 订单金额 sales"
    column_asset = _asset(assets, "column", _asset_id("fact_orders.payment_amount"))
    assert "支付金额" in column_asset.text
    assert "销售额" in column_asset.text
    assert "100" in column_asset.text
    assert column_asset.metadata["datasource"] == DEFAULT_DATASOURCE
    assert column_asset.metadata["aliases"] == ["销售额"]
    assert _asset(assets, "metric", _asset_id("sales_amount")).text.startswith("sales_amount 销售额")
    metric_alias_asset = _asset(assets, "metric", _asset_id("sales_amount:alias:营收总额"))
    assert metric_alias_asset.text == "营收总额"
    assert metric_alias_asset.metadata["name"] == "sales_amount"
    verified_query_asset = _asset(assets, "verified_query", _asset_id("recent_sales"))
    assert verified_query_asset.text == "recent_sales 最近销售额 销售额"
    assert verified_query_asset.metadata["sql"] == "SELECT SUM(payment_amount) FROM fact_orders"
    assert _asset(assets, "value", _asset_id("fact_orders.payment_amount:100")).text == "100"


def test_rebuild_vector_index_writes_rows_and_metadata(monkeypatch):
    session = _session_with_assets()
    vector_store = FakeVectorStore()
    monkeypatch.setattr(indexer, "get_settings", lambda: _settings())
    monkeypatch.setattr(indexer, "get_sqlite_engine", lambda: session.get_bind())
    monkeypatch.setattr(indexer, "sqlite_session", lambda: FakeSessionScope(session))
    monkeypatch.setattr(indexer, "get_embedding_dimension", lambda: 3)
    monkeypatch.setattr(indexer, "embed_texts", _fake_embed_texts)

    result = indexer.rebuild_vector_index(vector_store=vector_store, batch_size=2)

    assert vector_store.ensured_dimensions == [3]
    assert vector_store.clear_called is True
    assert len(vector_store.rows) == 12
    assert result.embedding_model == "D:/Models/BAAI/bge-m3"
    assert result.embedding_dimension == 3
    assert result.asset_counts == {
        "table": 1,
        "column": 2,
        "metric": 5,
        "verified_query": 1,
        "value": 3,
    }
    assert vector_store.metadata.embedding_model == "D:/Models/BAAI/bge-m3"
    assert vector_store.metadata.embedding_dimension == 3
    assert vector_store.metadata.asset_counts == result.asset_counts


def test_rebuild_vector_index_requires_vector_enabled(monkeypatch):
    monkeypatch.setattr(indexer, "get_settings", lambda: _settings(vector_enabled=False))

    with pytest.raises(RuntimeError, match="VECTOR_ENABLED"):
        indexer.rebuild_vector_index(vector_store=FakeVectorStore())


def test_rebuild_vector_index_uses_default_embedding_model_when_config_blank(monkeypatch):
    session = _session_with_assets()
    vector_store = FakeVectorStore()
    monkeypatch.setattr(indexer, "get_settings", lambda: _settings(embedding_model=None))
    monkeypatch.setattr(indexer, "get_sqlite_engine", lambda: session.get_bind())
    monkeypatch.setattr(indexer, "sqlite_session", lambda: FakeSessionScope(session))
    monkeypatch.setattr(indexer, "get_embedding_dimension", lambda: 3)
    monkeypatch.setattr(indexer, "embed_texts", _fake_embed_texts)

    result = indexer.rebuild_vector_index(vector_store=vector_store, batch_size=20)

    assert result.embedding_model == DEFAULT_EMBEDDING_MODEL
    assert vector_store.metadata.embedding_model == DEFAULT_EMBEDDING_MODEL


def _asset(assets, asset_type, asset_id):
    for asset in assets:
        if asset.asset_type == asset_type and asset.asset_id == asset_id:
            return asset
    raise AssertionError(f"Asset not found: {asset_type}:{asset_id}")


def _asset_id(local_asset_id: str) -> str:
    return f"{DEFAULT_DATASOURCE}:{local_asset_id}"


def _session_with_assets() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine, autoflush=False, expire_on_commit=False)
    table = MetaTable(
        table_name="fact_orders",
        display_name="订单事实表",
        description="订单金额",
        domain="sales",
        row_count=10,
        enabled=True,
    )
    disabled_table = MetaTable(
        table_name="raw_orders",
        display_name="原始订单",
        enabled=False,
    )
    session.add_all([table, disabled_table])
    session.flush()
    session.add_all(
        [
            MetaColumn(
                table_id=table.id,
                column_name="payment_amount",
                data_type="DOUBLE",
                description="支付金额",
                is_metric=True,
                sample_values=json.dumps([100, 200], ensure_ascii=False),
            ),
            MetaColumn(
                table_id=table.id,
                column_name="order_status",
                data_type="VARCHAR",
                description="订单状态",
                is_dimension=True,
                sample_values=json.dumps(["paid"], ensure_ascii=False),
            ),
            MetaColumn(
                table_id=disabled_table.id,
                column_name="order_id",
                data_type="VARCHAR",
                description="原始订单号",
                sample_values=json.dumps(["O1"], ensure_ascii=False),
            ),
        ]
    )
    session.add(
        MetaColumnAlias(
            table_name="fact_orders",
            column_name="payment_amount",
            alias="销售额",
        )
    )
    session.add(
        MetaMetric(
            name="sales_amount",
            label="销售额",
            expression="SUM(fact_orders.payment_amount)",
            description="支付金额合计",
            default_time_column="dim_date.date_value",
            allowed_dimensions=json.dumps(["date"], ensure_ascii=False),
            enabled=True,
        )
    )
    session.add(
        MetaVerifiedQuery(
            query_id="recent_sales",
            question="最近销售额",
            sql="SELECT SUM(payment_amount) FROM fact_orders",
            tags=json.dumps(["销售额"], ensure_ascii=False),
            verified_by="test",
            enabled=True,
        )
    )
    session.commit()
    return session


def _fake_embed_texts(texts):
    return [[float(index), float(index + 1), float(index + 2)] for index, _ in enumerate(texts)]


def _settings(vector_enabled=True, embedding_model="D:/Models/BAAI/bge-m3"):
    return SimpleNamespace(
        vector_enabled=vector_enabled,
        embedding_model=embedding_model,
    )


class FakeSessionScope:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeVectorStore:
    def __init__(self) -> None:
        self.ensured_dimensions = []
        self.clear_called = False
        self.rows = []
        self.metadata = None

    def ensure_tables(self, embedding_dimension):
        self.ensured_dimensions.append(embedding_dimension)

    def clear_vector_tables(self):
        self.clear_called = True

    def upsert_rows(self, rows):
        self.rows.extend(rows)

    def write_metadata(self, metadata):
        self.metadata = metadata
