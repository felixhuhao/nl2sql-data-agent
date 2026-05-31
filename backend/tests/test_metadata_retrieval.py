from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.metadata import retrieval
from backend.app.metadata.models import MetaColumn, MetaTable, create_metadata_schema
from backend.app.metadata.seed import seed_semantics


def test_retrieve_assets_matches_verified_query_metrics_and_tables(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("查询最近30天每日销售额和订单数")

    assert result["fallback_used"] is False
    assert {"sales_amount", "order_count"}.issubset(set(_names(result["metrics"], "name")))
    assert "recent_30d_daily_sales" in _names(result["verified_queries"], "id")
    assert {"fact_orders", "dim_date"}.issubset(set(_names(result["tables"], "table_name")))
    assert ("fact_orders", "payment_amount") in _column_keys(result)
    assert ("fact_orders", "order_id") in _column_keys(result)


def test_retrieve_assets_matches_channel_aliases(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("按渠道统计最近30天销售额")

    assert _item_by_name(result["tables"], "dim_channels", "table_name")["source"] == "direct_match"
    assert _item_by_name(result["tables"], "fact_orders", "table_name")["source"] in {
        "direct_match",
        "metric_expansion",
        "verified_query",
    }
    assert "sales_amount" in _names(result["metrics"], "name")
    assert "dim_channels" in _names(result["tables"], "table_name")
    assert ("fact_orders", "channel_key") in _column_keys(result)
    assert ("dim_channels", "channel_name") in _column_keys(result)


def test_retrieve_assets_matches_metric_label(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("客单价")

    assert result["metrics"][0]["name"] == "aov"
    assert _item_by_name(result["tables"], "fact_orders", "table_name")["source"] == "metric_expansion"
    assert {"fact_orders", "dim_date"}.issubset(set(_names(result["tables"], "table_name")))


def test_retrieve_assets_matches_sample_values(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("华东地区销售额")

    region_group = next(
        column
        for column in result["columns"]
        if column["table_name"] == "dim_regions" and column["column_name"] == "region_group"
    )
    assert "sample_value:华东" in region_group["reasons"]


def test_retrieve_assets_falls_back_to_allowed_tables_when_no_assets_match(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("完全无法命中的问题")

    assert result["fallback_used"] is True
    assert set(_names(result["tables"], "table_name")) == {
        "fact_orders",
        "dim_date",
        "dim_regions",
        "dim_channels",
    }
    assert {table["source"] for table in result["tables"]} == {"fallback"}
    assert result["columns"] == []
    assert result["metrics"] == []
    assert result["verified_queries"] == []


def test_retrieve_assets_applies_per_type_limits(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets(
        "查询最近30天每日销售额和订单数",
        table_limit=1,
        column_limit=2,
        metric_limit=1,
        verified_query_limit=1,
    )

    assert len(result["tables"]) == 1
    assert len(result["columns"]) == 2
    assert len(result["metrics"]) == 1
    assert len(result["verified_queries"]) == 1


def test_retrieve_assets_handles_empty_analysis_space(monkeypatch):
    _patch_retrieval_db(monkeypatch)

    result = retrieval.retrieve_metadata_assets("销售额")

    assert result["fallback_used"] is True
    assert result["tables"] == []
    assert result["columns"] == []
    assert result["metrics"] == []
    assert result["verified_queries"] == []


def test_retrieve_assets_can_force_vector_off(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)
    monkeypatch.setattr(retrieval, "get_settings", lambda: type("Settings", (), {"vector_enabled": True})())
    monkeypatch.setattr(
        retrieval,
        "hybrid_merge",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hybrid should not run")),
    )

    result = retrieval.retrieve_metadata_assets("销售额", use_vector=False)

    assert "sales_amount" in _names(result["metrics"], "name")


def test_retrieve_assets_uses_vector_before_fallback(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)
    monkeypatch.setattr(retrieval, "get_settings", lambda: type("Settings", (), {"vector_enabled": True})())

    def fake_hybrid(rule_result, question, **kwargs):
        return {
            **rule_result,
            "metrics": [{"name": "sales_amount", "score": 0.5, "reasons": ["vector:0.90"]}],
            "retrieval_meta": {"vector_used": True, "index_status": "ready", "sources": {}, "value_hits": []},
        }

    monkeypatch.setattr(retrieval, "hybrid_merge", fake_hybrid)

    result = retrieval.retrieve_metadata_assets("规则无法命中但向量命中", use_vector=None)

    assert result["fallback_used"] is False
    assert result["tables"] == []
    assert result["metrics"][0]["name"] == "sales_amount"


def _patch_retrieval_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)

    @contextmanager
    def session_scope():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(retrieval, "get_sqlite_engine", lambda: engine)
    monkeypatch.setattr(retrieval, "sqlite_session", session_scope)
    return engine


def _insert_demo_physical_metadata(engine) -> None:
    with Session(engine) as session:
        table_columns = {
            "fact_orders": [
                "order_id",
                "payment_amount",
                "date_key",
                "region_key",
                "channel_key",
            ],
            "dim_date": ["date_key", "date_value"],
            "dim_regions": ["region_key", "region_group"],
            "dim_channels": ["channel_key", "channel_name"],
        }
        for table_name, column_names in table_columns.items():
            table = MetaTable(table_name=table_name, enabled=True)
            session.add(table)
            session.flush()
            session.add_all(
                [
                    MetaColumn(table_id=table.id, column_name=column_name, data_type="VARCHAR")
                    for column_name in column_names
                ]
            )
        seed_semantics(session)
        session.commit()


def _names(items: list[dict], key: str) -> list[str]:
    return [item[key] for item in items]


def _item_by_name(items: list[dict], value: str, key: str) -> dict:
    return next(item for item in items if item[key] == value)


def _column_keys(result: dict) -> set[tuple[str, str]]:
    return {(column["table_name"], column["column_name"]) for column in result["columns"]}
