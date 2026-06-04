import json
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.api import metadata as metadata_api
from backend.app.metadata import service
from backend.app.metadata.models import (
    DEFAULT_DATASOURCE,
    MetaAnalysisSpace,
    MetaColumn,
    MetaMetric,
    MetaTable,
    MetaVerifiedQuery,
    create_metadata_schema,
)
from backend.app.metadata.seed import seed_semantics


CLICKHOUSE_DATASOURCE = "clickhouse_ecommerce"


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
            "enabled": True,
        },
        {
            "id": "disabled_query",
            "question": "禁用问题",
            "sql": "SELECT 1",
            "tags": ["disabled"],
            "verified_by": "system",
            "enabled": False,
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


def test_build_focused_context_uses_retrieved_assets_and_join_keys(monkeypatch):
    engine = _patch_service_db(monkeypatch)
    _insert_demo_context_assets(engine)
    retrieval_result = {
        "question": "按渠道统计最近30天销售额",
        "fallback_used": False,
        "tables": [
            {"table_name": "dim_channels", "source": "direct_match"},
            {"table_name": "fact_orders", "source": "metric_expansion"},
        ],
        "columns": [
            {"table_name": "dim_channels", "column_name": "channel_name"},
            {"table_name": "fact_orders", "column_name": "payment_amount"},
        ],
        "metrics": [{"name": "sales_amount"}],
        "verified_queries": [],
    }

    focused_context = service.build_focused_context_from_retrieval(retrieval_result)
    full_context = service.build_schema_context()

    assert len(focused_context) < len(full_context)
    assert "- fact_orders:" in focused_context
    assert "- dim_channels:" in focused_context
    assert "- dim_regions:" not in focused_context
    assert "  - payment_amount" in focused_context
    assert "  - channel_name" in focused_context
    assert "  - channel_key" in focused_context
    assert "fact_orders.channel_key -> dim_channels.channel_key" in focused_context
    assert "销售额 (sales_amount) = SUM(fact_orders.payment_amount)" in focused_context


def test_build_focused_context_expands_dimension_match_to_fact_partner(monkeypatch):
    engine = _patch_service_db(monkeypatch)
    _insert_demo_context_assets(engine)
    retrieval_result = {
        "question": "按渠道统计",
        "fallback_used": False,
        "tables": [{"table_name": "dim_channels", "source": "direct_match"}],
        "columns": [{"table_name": "dim_channels", "column_name": "channel_name"}],
        "metrics": [],
        "verified_queries": [],
    }

    focused_context = service.build_focused_context_from_retrieval(retrieval_result)

    assert "- dim_channels:" in focused_context
    assert "- fact_orders:" in focused_context
    assert "fact_orders.channel_key -> dim_channels.channel_key" in focused_context
    assert "  - channel_key" in focused_context


def test_build_focused_context_fallback_returns_full_schema_context(monkeypatch):
    engine = _patch_service_db(monkeypatch)
    _insert_demo_context_assets(engine)
    retrieval_result = {
        "question": "完全无法命中的问题",
        "fallback_used": True,
        "tables": [{"table_name": "fact_orders", "source": "fallback"}],
        "columns": [],
        "metrics": [],
        "verified_queries": [],
    }

    assert service.build_focused_context_from_retrieval(retrieval_result) == service.build_schema_context()


def test_build_focused_context_retrieves_question(monkeypatch):
    engine = _patch_service_db(monkeypatch)
    _insert_demo_context_assets(engine)
    retrieval_result = {
        "question": "客单价",
        "fallback_used": False,
        "tables": [{"table_name": "fact_orders", "source": "metric_expansion"}],
        "columns": [{"table_name": "fact_orders", "column_name": "payment_amount"}],
        "metrics": [{"name": "aov"}],
        "verified_queries": [],
    }
    captured = {}

    def fake_retrieve(question, **kwargs):
        captured["question"] = question
        captured["datasource_name"] = kwargs.get("datasource_name")
        return retrieval_result

    monkeypatch.setattr(service, "retrieve_metadata_assets", fake_retrieve)

    focused_context = service.build_focused_context("客单价")

    assert captured == {"question": "客单价", "datasource_name": DEFAULT_DATASOURCE}
    assert "客单价 (aov)" in focused_context
    assert "- fact_orders:" in focused_context


def test_build_focused_context_filters_columns_by_datasource(monkeypatch):
    engine = _patch_service_db(monkeypatch)
    with Session(engine) as session:
        duckdb_table = MetaTable(datasource=DEFAULT_DATASOURCE, table_name="fact_orders", enabled=True)
        clickhouse_table = MetaTable(datasource=CLICKHOUSE_DATASOURCE, table_name="fact_orders", enabled=True)
        session.add_all([duckdb_table, clickhouse_table])
        session.flush()
        session.add_all(
            [
                MetaColumn(
                    datasource=DEFAULT_DATASOURCE,
                    table_id=duckdb_table.id,
                    column_name="payment_amount",
                    data_type="DECIMAL",
                    description="duckdb amount",
                ),
                MetaColumn(
                    datasource=CLICKHOUSE_DATASOURCE,
                    table_id=clickhouse_table.id,
                    column_name="payment_amount",
                    data_type="Decimal(12,2)",
                    description="clickhouse amount",
                ),
                MetaAnalysisSpace(
                    name="clickhouse_space",
                    datasource=CLICKHOUSE_DATASOURCE,
                    tables=json.dumps(["fact_orders"], ensure_ascii=False),
                    enabled_metrics=json.dumps([], ensure_ascii=False),
                    allowed_operations=json.dumps(["select"], ensure_ascii=False),
                    enabled=True,
                ),
            ]
        )
        session.commit()
    retrieval_result = {
        "question": "销售额",
        "fallback_used": False,
        "tables": [{"table_name": "fact_orders", "source": "direct_match"}],
        "columns": [{"table_name": "fact_orders", "column_name": "payment_amount"}],
        "metrics": [],
        "verified_queries": [],
    }

    focused_context = service.build_focused_context_from_retrieval(
        retrieval_result,
        datasource_name=CLICKHOUSE_DATASOURCE,
    )

    assert "clickhouse amount" in focused_context
    assert "duckdb amount" not in focused_context


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


def _insert_demo_context_assets(engine) -> None:
    with Session(engine) as session:
        table_columns = {
            "fact_orders": [
                "order_id",
                "payment_amount",
                "date_key",
                "channel_key",
                "region_key",
            ],
            "dim_channels": ["channel_key", "channel_name"],
            "dim_date": ["date_key", "date_value"],
            "dim_regions": ["region_key", "region_group"],
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
