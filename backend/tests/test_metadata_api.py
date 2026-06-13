import json
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app import main
from backend.app.metadata import retrieval, service
from backend.app.metadata.models import (
    MetaAnalysisSpace,
    MetaColumn,
    MetaColumnAlias,
    MetaMetric,
    MetaRelationship,
    MetaTable,
    MetaVerifiedQuery,
    create_metadata_schema,
)


def test_retrieve_metadata_endpoint_returns_retrieval_result(monkeypatch):
    def fake_retrieve(question: str, datasource_name: str = "duckdb_ecommerce") -> dict:
        assert question == "按渠道统计销售额"
        assert datasource_name == "duckdb_ecommerce"
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


def test_metadata_endpoints_pass_datasource_parameter(monkeypatch):
    captured = {}

    def fake_list_tables(datasource_name: str = "duckdb_ecommerce") -> list[dict]:
        captured["tables"] = datasource_name
        return [{"table_name": "fact_orders"}]

    def fake_list_metrics(enabled=None, datasource_name: str = "duckdb_ecommerce") -> list[dict]:
        captured["metrics"] = (enabled, datasource_name)
        return []

    def fake_retrieve(question: str, datasource_name: str = "duckdb_ecommerce") -> dict:
        captured["retrieve"] = (question, datasource_name)
        return {"question": question, "metrics": []}

    monkeypatch.setattr("backend.app.api.metadata.list_tables", fake_list_tables)
    monkeypatch.setattr("backend.app.api.metadata.list_metrics", fake_list_metrics)
    monkeypatch.setattr("backend.app.api.metadata.retrieve_metadata_assets", fake_retrieve)
    client = TestClient(main.app)

    table_response = client.get("/api/metadata/tables", params={"datasource": "clickhouse_ecommerce"})
    metric_response = client.get(
        "/api/metadata/metrics",
        params={"enabled": True, "datasource": "clickhouse_ecommerce"},
    )
    retrieve_response = client.get(
        "/api/metadata/retrieve",
        params={"question": "销售额", "datasource": "clickhouse_ecommerce"},
    )

    assert table_response.status_code == 200
    assert metric_response.status_code == 200
    assert retrieve_response.status_code == 200
    assert captured == {
        "tables": "clickhouse_ecommerce",
        "metrics": (True, "clickhouse_ecommerce"),
        "retrieve": ("销售额", "clickhouse_ecommerce"),
    }


def test_retrieve_metadata_endpoint_rejects_blank_question():
    client = TestClient(main.app)

    response = client.get("/api/metadata/retrieve", params={"question": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "question is required"


def test_vector_status_endpoint_returns_index_status(monkeypatch):
    monkeypatch.setattr(
        "backend.app.api.metadata.get_vector_index_status",
        lambda: {
            "vector_enabled": False,
            "status": "disabled",
            "asset_counts": {},
        },
    )
    client = TestClient(main.app)

    response = client.get("/api/metadata/vector/status")

    assert response.status_code == 200
    assert response.json() == {
        "vector_enabled": False,
        "status": "disabled",
        "asset_counts": {},
    }


def test_rebuild_vector_index_endpoint_returns_build_result(monkeypatch):
    monkeypatch.setattr(
        "backend.app.api.metadata.rebuild_vector_index_payload",
        lambda: {
            "embedding_model": "/models/custom-embedding-model",
            "embedding_dimension": 768,
            "built_at": "2026-05-31T10:00:00Z",
            "asset_counts": {"metric": 3},
        },
    )
    client = TestClient(main.app)

    response = client.post("/api/metadata/vector/rebuild")

    assert response.status_code == 200
    assert response.json()["asset_counts"] == {"metric": 3}


def test_create_alias_endpoint_makes_retrieval_hit_alias(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    _capture_vector_stale_reasons(monkeypatch)
    _disable_retrieval_vector(monkeypatch)
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


def test_admin_mutation_marks_vector_index_stale(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    stale_reasons = _capture_vector_stale_reasons(monkeypatch)
    client = TestClient(main.app)

    response = client.post(
        "/api/metadata/aliases",
        json={
            "table_name": "fact_orders",
            "column_name": "payment_amount",
            "alias": "成交金额",
        },
    )

    assert response.status_code == 200
    assert stale_reasons == ["Alias changed."]


def test_create_alias_endpoint_rejects_duplicate_and_invalid_column(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    _capture_vector_stale_reasons(monkeypatch)
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


def test_delete_alias_endpoint_is_datasource_scoped(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    stale_reasons = _capture_vector_stale_reasons(monkeypatch)
    client = TestClient(main.app)

    create_response = client.post(
        "/api/metadata/aliases",
        json={
            "table_name": "fact_orders",
            "column_name": "payment_amount",
            "alias": "成交金额",
        },
    )
    alias_id = create_response.json()["id"]
    wrong_datasource_response = client.delete(
        f"/api/metadata/aliases/{alias_id}",
        params={"datasource": "clickhouse_ecommerce"},
    )
    default_aliases_response = client.get("/api/metadata/aliases")
    delete_response = client.delete(f"/api/metadata/aliases/{alias_id}")
    empty_aliases_response = client.get("/api/metadata/aliases")

    assert create_response.status_code == 200
    assert wrong_datasource_response.status_code == 404
    assert [alias["id"] for alias in default_aliases_response.json()] == [alias_id]
    assert delete_response.status_code == 204
    assert empty_aliases_response.json() == []
    assert stale_reasons == ["Alias changed.", "Alias changed."]


def test_metric_endpoints_create_update_toggle_and_filter(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    _capture_vector_stale_reasons(monkeypatch)
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


def test_verified_query_endpoints_validate_sql_and_affect_retrieval(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    _capture_vector_stale_reasons(monkeypatch)
    _disable_retrieval_vector(monkeypatch)
    client = TestClient(main.app)

    create_response = client.post(
        "/api/metadata/verified-queries",
        json={
            "query_id": "admin_verified_sales",
            "question": "管理端销售额",
            "sql": "SELECT payment_amount FROM fact_orders",
            "tags": ["admin", "sales"],
        },
    )
    retrieve_response = client.get("/api/metadata/retrieve", params={"question": "管理端销售额"})
    unsafe_response = client.post(
        "/api/metadata/verified-queries",
        json={
            "query_id": "unsafe_verified",
            "question": "危险查询",
            "sql": "DELETE FROM fact_orders",
        },
    )

    assert create_response.status_code == 200
    assert create_response.json()["enabled"] is True
    assert "admin_verified_sales" in [query["id"] for query in retrieve_response.json()["verified_queries"]]
    assert unsafe_response.status_code == 422
    assert "operation_guard" in unsafe_response.json()["detail"]


def test_verified_query_update_toggle_and_filter(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    _capture_vector_stale_reasons(monkeypatch)
    client = TestClient(main.app)
    client.post(
        "/api/metadata/verified-queries",
        json={
            "query_id": "admin_verified_sales",
            "question": "管理端销售额",
            "sql": "SELECT payment_amount FROM fact_orders",
        },
    )

    update_response = client.put(
        "/api/metadata/verified-queries/admin_verified_sales",
        json={
            "question": "更新后的销售额",
            "tags": ["updated"],
            "sql": "SELECT order_id, payment_amount FROM fact_orders",
        },
    )
    toggle_response = client.patch("/api/metadata/verified-queries/admin_verified_sales/toggle")
    default_response = client.get("/api/metadata/verified-queries")
    enabled_response = client.get("/api/metadata/verified-queries", params={"enabled": True})
    disabled_response = client.get("/api/metadata/verified-queries", params={"enabled": False})

    assert update_response.status_code == 200
    assert update_response.json()["question"] == "更新后的销售额"
    assert update_response.json()["tags"] == ["updated"]
    assert toggle_response.status_code == 200
    assert toggle_response.json()["enabled"] is False
    assert [query["id"] for query in default_response.json()] == ["admin_verified_sales"]
    assert enabled_response.json() == []
    assert [query["id"] for query in disabled_response.json()] == ["admin_verified_sales"]


def test_update_analysis_space_endpoint_validates_assets_and_operations(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    _capture_vector_stale_reasons(monkeypatch)
    client = TestClient(main.app)

    update_response = client.put(
        "/api/metadata/analysis-space",
        json={
            "tables": ["fact_orders"],
            "enabled_metrics": ["existing_metric"],
            "allowed_operations": ["select"],
        },
    )
    invalid_table_response = client.put(
        "/api/metadata/analysis-space",
        json={"tables": ["missing_table"]},
    )
    invalid_operation_response = client.put(
        "/api/metadata/analysis-space",
        json={"allowed_operations": ["delete"]},
    )

    assert update_response.status_code == 200
    assert update_response.json()["tables"] == ["fact_orders"]
    assert update_response.json()["enabled_metrics"] == ["existing_metric"]
    assert invalid_table_response.status_code == 422
    assert invalid_operation_response.status_code == 422


def test_relationship_endpoint_updates_metadata_only(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    stale_reasons = _capture_vector_stale_reasons(monkeypatch)
    client = TestClient(main.app)

    relationships_response = client.get("/api/metadata/relationships")
    relationship_id = relationships_response.json()[0]["id"]
    wrong_datasource_response = client.put(
        f"/api/metadata/relationships/{relationship_id}",
        params={"datasource": "clickhouse_ecommerce"},
        json={"confidence": 1.0},
    )
    update_response = client.put(
        f"/api/metadata/relationships/{relationship_id}",
        json={
            "confidence": 1.0,
            "fanout_risk": "medium",
            "source": "overlay",
            "description": "",
        },
    )
    invalid_response = client.put(
        f"/api/metadata/relationships/{relationship_id}",
        json={"source": "confirmed"},
    )

    assert relationships_response.status_code == 200
    assert wrong_datasource_response.status_code == 404
    assert update_response.status_code == 200
    assert update_response.json()["confidence"] == 1.0
    assert update_response.json()["fanout_risk"] == "medium"
    assert update_response.json()["source"] == "overlay"
    assert update_response.json()["description"] is None
    assert invalid_response.status_code == 422
    assert stale_reasons == ["Relationship changed."]


def test_validate_metadata_endpoint_accepts_valid_assets(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    with Session(engine) as session:
        session.add(
            MetaMetric(
                name="gross_sales",
                label="总销售额",
                expression="SUM(fact_orders.payment_amount)",
                default_time_column="dim_date.date_value",
                enabled=True,
            )
        )
        session.commit()
    client = TestClient(main.app)

    response = client.get("/api/metadata/validate")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "issues": []}


def test_validate_metadata_endpoint_reports_stale_assets(monkeypatch):
    engine = _patch_metadata_db(monkeypatch)
    _insert_admin_api_assets(engine)
    with Session(engine) as session:
        analysis_space = session.query(MetaAnalysisSpace).one()
        analysis_space.tables = json.dumps(["fact_orders", "missing_table"], ensure_ascii=False)
        analysis_space.enabled_metrics = json.dumps(["missing_metric"], ensure_ascii=False)
        analysis_space.allowed_operations = json.dumps(["select", "delete"], ensure_ascii=False)
        session.add_all(
            [
                MetaColumnAlias(
                    table_name="fact_orders",
                    column_name="missing_column",
                    alias="坏别名",
                ),
                MetaMetric(
                    name="bad_metric",
                    label="坏指标",
                    expression="SUM(fact_orders.missing_amount)",
                    default_time_column="dim_date.missing_date",
                    enabled=True,
                ),
                MetaRelationship(
                    source_table="fact_orders",
                    source_column="missing_key",
                    target_table="dim_date",
                    target_column="date_key",
                    relationship_type="many_to_one",
                    source="overlay",
                    confidence=1.0,
                    fanout_risk="low",
                ),
                MetaVerifiedQuery(
                    query_id="bad_verified",
                    question="坏查询",
                    sql="SELECT order_id FROM raw_orders",
                    tags=json.dumps(["bad"], ensure_ascii=False),
                    verified_by="tester",
                    enabled=True,
                ),
            ]
        )
        session.commit()
    client = TestClient(main.app)

    response = client.get("/api/metadata/validate")
    payload = response.json()
    issue_keys = {
        (issue["asset_type"], issue["asset_id"], issue["field"])
        for issue in payload["issues"]
    }

    assert response.status_code == 200
    assert payload["ok"] is False
    assert ("analysis_space", "admin_test", "tables") in issue_keys
    assert ("analysis_space", "admin_test", "enabled_metrics") in issue_keys
    assert ("analysis_space", "admin_test", "allowed_operations") in issue_keys
    assert ("metric", "bad_metric", "expression") in issue_keys
    assert ("metric", "bad_metric", "default_time_column") in issue_keys
    assert any(issue["asset_type"] == "alias" for issue in payload["issues"])
    assert any(issue["asset_type"] == "relationship" for issue in payload["issues"])
    assert ("verified_query", "bad_verified", "sql") in issue_keys


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


def _capture_vector_stale_reasons(monkeypatch) -> list[str]:
    stale_reasons: list[str] = []
    monkeypatch.setattr("backend.app.api.metadata.mark_vector_index_stale", stale_reasons.append)
    return stale_reasons


def _disable_retrieval_vector(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.metadata.retrieval.get_settings",
        lambda: type("Settings", (), {"vector_enabled": False})(),
    )


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
            MetaMetric(
                name="existing_metric",
                label="已有指标",
                expression="SUM(fact_orders.payment_amount)",
                enabled=True,
            )
        )
        session.add(
            MetaRelationship(
                source_table="fact_orders",
                source_column="date_key",
                target_table="dim_date",
                target_column="date_key",
                relationship_type="many_to_one",
                source="inferred",
                confidence=0.8,
                fanout_risk="low",
            )
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
