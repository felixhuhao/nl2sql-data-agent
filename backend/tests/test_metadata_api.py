import json
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app import main
from backend.app.metadata import retrieval, service
from backend.app.metadata.models import MetaAnalysisSpace, MetaColumn, MetaTable, create_metadata_schema


def test_retrieve_metadata_endpoint_returns_retrieval_result(monkeypatch):
    def fake_retrieve(question: str) -> dict:
        assert question == "按渠道统计销售额"
        return {
            "question": question,
            "normalized_question": "按渠道统计销售额",
            "fallback_used": False,
            "tables": [{"table_name": "fact_orders", "source": "metric_expansion"}],
            "columns": [{"table_name": "fact_orders", "column_name": "payment_amount"}],
            "metrics": [{"name": "sales_amount"}],
            "verified_queries": [],
        }

    monkeypatch.setattr("backend.app.api.metadata.retrieve_metadata_assets", fake_retrieve)
    client = TestClient(main.app)

    response = client.get("/api/metadata/retrieve", params={"question": "按渠道统计销售额"})

    assert response.status_code == 200
    assert response.json()["metrics"] == [{"name": "sales_amount"}]
    assert response.json()["tables"][0]["source"] == "metric_expansion"


def test_retrieve_metadata_endpoint_rejects_blank_question():
    client = TestClient(main.app)

    response = client.get("/api/metadata/retrieve", params={"question": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "question is required"


def test_create_alias_endpoint_makes_retrieval_hit_alias(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    client = TestClient(main.app)

    create_response = client.post(
        "/api/metadata/aliases",
        json={
            "table_name": "fact_orders",
            "column_name": "payment_amount",
            "alias": "成交金额",
        },
    )
    retrieve_response = client.get("/api/metadata/retrieve", params={"question": "成交金额"})

    assert create_response.status_code == 200
    assert create_response.json()["alias"] == "成交金额"
    assert retrieve_response.status_code == 200
    assert {
        (column["table_name"], column["column_name"])
        for column in retrieve_response.json()["columns"]
    } == {("fact_orders", "payment_amount")}


def test_create_alias_endpoint_rejects_duplicate_and_invalid_column(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    client = TestClient(main.app)
    payload = {
        "table_name": "fact_orders",
        "column_name": "payment_amount",
        "alias": "成交金额",
    }

    first_response = client.post("/api/metadata/aliases", json=payload)
    duplicate_response = client.post("/api/metadata/aliases", json=payload)
    invalid_response = client.post(
        "/api/metadata/aliases",
        json={
            "table_name": "fact_orders",
            "column_name": "missing_column",
            "alias": "不存在",
        },
    )

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 409
    assert invalid_response.status_code == 422


def test_metric_endpoints_create_update_toggle_and_filter(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    client = TestClient(main.app)

    create_response = client.post(
        "/api/metadata/metrics",
        json={
            "name": "gross_sales",
            "label": "总销售额",
            "expression": "SUM(fact_orders.payment_amount)",
            "default_time_column": "dim_date.date_value",
            "allowed_dimensions": ["date"],
        },
    )
    update_response = client.put(
        "/api/metadata/metrics/gross_sales",
        json={"label": "成交额", "description": "", "allowed_dimensions": ["date", "channel"]},
    )
    toggle_response = client.patch("/api/metadata/metrics/gross_sales/toggle")
    disabled_response = client.get("/api/metadata/metrics", params={"enabled": False})
    context_response = client.get("/api/metadata/schema-context")

    assert create_response.status_code == 200
    assert create_response.json()["enabled"] is True
    assert update_response.status_code == 200
    assert update_response.json()["label"] == "成交额"
    assert update_response.json()["description"] is None
    assert update_response.json()["allowed_dimensions"] == ["date", "channel"]
    assert toggle_response.status_code == 200
    assert toggle_response.json()["enabled"] is False
    assert [metric["name"] for metric in disabled_response.json()] == ["gross_sales"]
    assert "成交额 (gross_sales)" not in context_response.json()["schema_context"]


def test_create_metric_endpoint_rejects_invalid_expression(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    client = TestClient(main.app)

    response = client.post(
        "/api/metadata/metrics",
        json={
            "name": "bad_metric",
            "label": "错误指标",
            "expression": "SUM(fact_orders.missing_amount)",
        },
    )

    assert response.status_code == 422
    assert "Column not found" in response.json()["detail"]


def _patch_metadata_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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

    monkeypatch.setattr(service, "get_sqlite_engine", lambda: engine)
    monkeypatch.setattr(service, "sqlite_session", session_scope)
    monkeypatch.setattr(retrieval, "get_sqlite_engine", lambda: engine)
    monkeypatch.setattr(retrieval, "sqlite_session", session_scope)
    return engine


def _insert_admin_api_assets(engine) -> None:
    with Session(engine) as session:
        table_columns = {
            "fact_orders": ["order_id", "payment_amount", "date_key", "channel_key"],
            "dim_date": ["date_key", "date_value"],
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
        session.add(
            MetaAnalysisSpace(
                name="admin_test",
                datasource="duckdb_test",
                tables=json.dumps(["fact_orders", "dim_date"], ensure_ascii=False),
                enabled_metrics=json.dumps(["gross_sales"], ensure_ascii=False),
                allowed_operations=json.dumps(["select"], ensure_ascii=False),
                enabled=True,
            )
        )
        session.commit()
