import json
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.api import metadata as metadata_api
from backend.app.metadata import service
from backend.app.metadata.models import (
    MetaAnalysisSpace,
    MetaColumn,
    MetaMetric,
    MetaTable,
    MetaVerifiedQuery,
    create_metadata_schema,
)


def test_build_schema_context_reads_runtime_assets_from_db(monkeypatch):
    engine = _patch_service_db(monkeypatch)
    _insert_runtime_assets(engine)

    schema_context = service.build_schema_context()

    assert "name = custom_space" in schema_context
    assert "datasource = custom_datasource" in schema_context
    assert "自定义指标 (custom_metric) = SUM(fact_orders.payment_amount)" in schema_context
    assert "- id: custom_query" in schema_context
    assert "disabled_query" not in schema_context
    assert "ignored_metric" not in schema_context


def test_build_explainability_context_reads_runtime_assets_from_db(monkeypatch):
    engine = _patch_service_db(monkeypatch)
    _insert_runtime_assets(engine)

    context = service.build_explainability_context()

    assert context["analysis_space"]["name"] == "custom_space"
    assert context["analysis_space"]["tables"] == ["fact_orders"]
    assert context["metrics"][0]["name"] == "custom_metric"
    assert context["metrics"][0]["allowed_dimensions"] == ["date"]
    assert context["verified_queries"][0]["id"] == "custom_query"
    assert context["verified_queries"][0]["tags"] == ["custom"]


def test_metadata_api_runtime_assets_read_from_db(monkeypatch):
    engine = _patch_service_db(monkeypatch)
    _insert_runtime_assets(engine)

    analysis_space = metadata_api.analysis_space_endpoint()
    verified_queries = metadata_api.verified_queries_endpoint()

    assert analysis_space["name"] == "custom_space"
    assert analysis_space["enabled_metrics"] == ["custom_metric"]
    assert verified_queries == [
        {
            "id": "custom_query",
            "question": "自定义问题",
            "sql": "SELECT payment_amount FROM fact_orders",
            "tags": ["custom"],
            "verified_by": "tester",
        }
    ]


def test_runtime_context_handles_empty_analysis_space(monkeypatch):
    _patch_service_db(monkeypatch)

    schema_context = service.build_schema_context()
    explainability_context = service.build_explainability_context()

    assert "name = " in schema_context
    assert "allowed_tables = " in schema_context
    assert "## Tables" in schema_context
    assert "## Metric Definitions" not in schema_context
    assert explainability_context["analysis_space"] == {
        "name": "",
        "datasource": "",
        "tables": [],
        "allowed_tables": [],
        "enabled_metrics": [],
        "allowed_operations": [],
    }
    assert explainability_context["tables"] == []
    assert explainability_context["metrics"] == []
    assert explainability_context["join_paths"] == []
    assert explainability_context["verified_queries"] == []


def _patch_service_db(monkeypatch):
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

    monkeypatch.setattr(service, "get_sqlite_engine", lambda: engine)
    monkeypatch.setattr(service, "sqlite_session", session_scope)
    return engine


def _insert_runtime_assets(engine) -> None:
    with Session(engine) as session:
        table = MetaTable(
            table_name="fact_orders",
            display_name="订单表",
            description="订单事实",
            row_count=2,
        )
        session.add(table)
        session.flush()
        session.add(
            MetaColumn(
                table_id=table.id,
                column_name="payment_amount",
                data_type="DECIMAL",
                description="支付金额",
                is_metric=True,
            )
        )
        session.add_all(
            [
                MetaMetric(
                    name="custom_metric",
                    label="自定义指标",
                    expression="SUM(fact_orders.payment_amount)",
                    description="测试指标",
                    default_time_column="dim_date.date_value",
                    allowed_dimensions=json.dumps(["date"], ensure_ascii=False),
                    enabled=True,
                ),
                MetaMetric(
                    name="ignored_metric",
                    label="忽略指标",
                    expression="COUNT(*)",
                    enabled=True,
                ),
                MetaAnalysisSpace(
                    name="custom_space",
                    datasource="custom_datasource",
                    tables=json.dumps(["fact_orders"], ensure_ascii=False),
                    enabled_metrics=json.dumps(["custom_metric"], ensure_ascii=False),
                    allowed_operations=json.dumps(["select"], ensure_ascii=False),
                    enabled=True,
                ),
                MetaVerifiedQuery(
                    query_id="custom_query",
                    question="自定义问题",
                    sql="SELECT payment_amount FROM fact_orders",
                    tags=json.dumps(["custom"], ensure_ascii=False),
                    verified_by="tester",
                    enabled=True,
                ),
                MetaVerifiedQuery(
                    query_id="disabled_query",
                    question="禁用问题",
                    sql="SELECT 1",
                    tags=json.dumps(["disabled"], ensure_ascii=False),
                    enabled=False,
                ),
            ]
        )
        session.commit()
