import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from backend.app import main
import backend.app.agent.nodes as nodes_module
from backend.app.api.chat import iter_chat_events
from backend.app.connectors.schema import DataSourceInfo
from backend.app.core.llm_provider import MockLLMProvider, SQLGenerationResult
from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult
from backend.app.sql_guard.scope import GuardScope


def test_iter_chat_events_returns_step_and_done_events_for_demo_question():
    executed_sql = []

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        assert datasource_name == "duckdb_ecommerce"
        executed_sql.append(guard_result.normalized_sql)
        return QueryResult(
            columns=["date_value", "sales_amount", "order_count"],
            rows=[["2025-12-31", 100, 2]],
            row_count=1,
        )

    events = _parse_events(
        iter_chat_events(
            "查询最近30天每日销售额和订单数",
            provider=MockLLMProvider(),
            schema_context_builder=lambda: "# Schema Context",
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    assert [event["event"] for event in events] == [
        "step",
        "step",
        "step",
        "step",
        "step",
        "step",
        "step",
        "step",
        "step",
        "done",
    ]
    assert [event["data"].get("step") for event in events[:-1]] == [
        "datasource_selected",
        "intent_guard",
        "build_context",
        "olap_detected",
        "generate_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]
    assert executed_sql
    assert events[-1]["data"]["result"]["row_count"] == 1
    assert events[-1]["data"]["datasource"] == {
        "name": "duckdb_ecommerce",
        "dialect": "duckdb",
        "display_name": "DuckDB (本地)",
    }
    assert events[-1]["data"]["chart_recommendation"]["chart_type"] == "line"
    assert events[-1]["data"]["explainability"]["guard_result"]["allowed"] is True
    assert events[-1]["data"]["olap_intents"] == []
    assert events[-1]["data"]["olap_description"] == "未检测到 OLAP 分析意图"


def test_iter_chat_events_returns_retrieve_context_step(monkeypatch):
    monkeypatch.setattr(
        nodes_module,
        "build_focused_context_from_retrieval",
        lambda retrieval_result, datasource_name: "# Focused Schema Context",
    )

    def fake_retriever(question: str, datasource_name: str) -> dict:
        assert question == "查询最近30天每日销售额和订单数"
        assert datasource_name == "duckdb_ecommerce"
        return {
            "question": question,
            "fallback_used": False,
            "tables": [{"table_name": "fact_orders"}],
            "columns": [{"table_name": "fact_orders", "column_name": "payment_amount"}],
            "metrics": [{"name": "sales_amount"}],
            "verified_queries": [{"id": "recent_30d_daily_sales"}],
            "retrieval_meta": {
                "vector_used": True,
                "index_status": "ready",
                "stale_reason": None,
                "sources": {"metric:sales_amount": ["vector:0.91"]},
                "value_hits": [
                    {
                        "table_name": "dim_regions",
                        "column_name": "region_group",
                        "matched_value": "华东",
                        "source": "exact",
                        "score": 1.0,
                    }
                ],
            },
        }

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        return QueryResult(
            columns=["date_value", "sales_amount", "order_count"],
            rows=[["2025-12-31", 100, 2]],
            row_count=1,
        )

    events = _parse_events(
        iter_chat_events(
            "查询最近30天每日销售额和订单数",
            provider=MockLLMProvider(),
            retriever=fake_retriever,
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    assert events[0] == {
        "event": "step",
        "data": {
            "step": "datasource_selected",
            "status": "completed",
            "name": "duckdb_ecommerce",
            "dialect": "duckdb",
            "display_name": "DuckDB (本地)",
        },
    }
    assert events[1] == {
        "event": "step",
        "data": {
            "step": "intent_guard",
            "status": "completed",
        },
    }
    assert events[2] == {
        "event": "step",
        "data": {
            "step": "retrieve_context",
            "status": "completed",
            "fallback_used": False,
            "tables": ["fact_orders"],
            "columns": ["fact_orders.payment_amount"],
            "metrics": ["sales_amount"],
            "verified_queries": ["recent_30d_daily_sales"],
            "vector_used": True,
            "index_status": "ready",
            "stale_reason": None,
            "value_hits": [
                {
                    "table_name": "dim_regions",
                    "column_name": "region_group",
                    "matched_value": "华东",
                    "source": "exact",
                    "score": 1.0,
                }
            ],
            "retrieval_sources": {"metric:sales_amount": ["vector:0.91"]},
        },
    }
    assert [event["data"].get("step") for event in events[:-1]] == [
        "datasource_selected",
        "intent_guard",
        "retrieve_context",
        "build_context",
        "olap_detected",
        "generate_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]


def test_iter_chat_events_returns_olap_detected_step_for_composite_intent():
    class SelectProvider:
        name = "select-provider"

        def generate_sql(self, request):
            return SQLGenerationResult(sql="SELECT payment_amount FROM fact_orders", provider=self.name)

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        return QueryResult(columns=["payment_amount"], rows=[[100]], row_count=1)

    events = _parse_events(
        iter_chat_events(
            "查询销售额前10的商品同比增长",
            provider=SelectProvider(),
            schema_context_builder=lambda: "# Schema Context",
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    olap_step = next(event for event in events if event["data"].get("step") == "olap_detected")
    assert olap_step["data"] == {
        "step": "olap_detected",
        "status": "completed",
        "olap_intents": ["topn", "yoy_mom"],
        "description": "检测到 TopN / 排名 / 分层分析意图；检测到同比 / 环比分析意图",
    }
    assert events[-1]["data"]["olap_intents"] == ["topn", "yoy_mom"]
    assert events[-1]["data"]["olap_description"] == "检测到 TopN / 排名 / 分层分析意图；检测到同比 / 环比分析意图"


def test_iter_chat_events_returns_error_event_for_destructive_intent():
    def failing_retriever(question: str) -> dict:
        raise AssertionError("retriever should not be called")

    events = _parse_events(
        iter_chat_events(
            "删除2024年的订单数据",
            provider=MockLLMProvider(),
            retriever=failing_retriever,
            scope_builder=_scope,
        )
    )

    assert [event["event"] for event in events] == ["step", "error"]
    assert events[-1]["data"]["step"] == "intent_guard"
    assert events[-1]["data"]["reason"] == "DELETE intent is not allowed."
    assert events[-1]["data"]["error_kind"] == "blocked"


def test_iter_chat_events_returns_error_for_unknown_datasource():
    events = _parse_events(
        iter_chat_events(
            "查询订单",
            datasource_name="missing_datasource",
            provider=MockLLMProvider(),
            scope_builder=_scope,
        )
    )

    assert [event["event"] for event in events] == ["error"]
    assert events[0]["data"]["step"] == "datasource_selected"
    assert "Unknown datasource" in events[0]["data"]["reason"]


def test_iter_chat_events_returns_error_event_for_guard_rejection():
    class DeleteProvider:
        name = "delete-provider"

        def generate_sql(self, request):
            return SQLGenerationResult(sql="DELETE FROM fact_orders", provider=self.name)

    executed = []

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        executed.append(guard_result)
        return QueryResult(columns=[], rows=[], row_count=0)

    events = _parse_events(
        iter_chat_events(
            "查询订单",
            provider=DeleteProvider(),
            schema_context_builder=lambda: "# Schema Context",
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    assert [event["event"] for event in events] == ["step", "step", "step", "step", "step", "step", "error"]
    assert events[-1]["data"]["step"] == "sql_guard"
    assert events[-1]["data"]["reason"] == "DELETE is not allowed."
    assert events[-1]["data"]["error_kind"] == "blocked"
    assert events[-1]["data"]["explainability"]["guard_result"]["allowed"] is False
    assert executed == []


def test_iter_chat_events_repairs_guard_rejection_and_returns_repair_step():
    class RepairProvider:
        name = "repair-provider"

        def generate_sql(self, request):
            if request.repair is None:
                return SQLGenerationResult(sql="SELECT product_id FROM fact_orders", provider=self.name)
            return SQLGenerationResult(sql="SELECT payment_amount FROM fact_orders", provider=self.name)

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        return QueryResult(
            columns=["payment_amount"],
            rows=[[100]],
            row_count=1,
        )

    events = _parse_events(
        iter_chat_events(
            "查询订单金额",
            provider=RepairProvider(),
            schema_context_builder=lambda: "# Schema Context",
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    assert [event["data"].get("step") for event in events[:-1]] == [
        "datasource_selected",
        "intent_guard",
        "build_context",
        "olap_detected",
        "generate_sql",
        "sql_guard",
        "repair_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]
    repair_step = next(event for event in events if event["data"].get("step") == "repair_sql")
    assert repair_step["data"]["attempt"] == 1
    assert repair_step["data"]["original_sql"] == "SELECT product_id FROM fact_orders"
    assert repair_step["data"]["repaired_sql"] == "SELECT payment_amount FROM fact_orders"
    assert repair_step["data"]["error_stage"] == "sql_guard"
    assert repair_step["data"]["error_kind"] == "scope_guard"
    assert events[-1]["data"]["repair_history"][0]["succeeded"] is True
    assert events[-1]["data"]["repair_history"][0]["final_stage"] == "execute"


def test_iter_chat_events_repairs_execution_failure_and_returns_repair_step():
    class CatalogException(Exception):
        pass

    class RepairProvider:
        name = "repair-provider"

        def generate_sql(self, request):
            if request.repair is None:
                return SQLGenerationResult(sql="SELECT payment_amount FROM fact_orders", provider=self.name)
            return SQLGenerationResult(sql="SELECT order_id FROM fact_orders", provider=self.name)

    calls = []

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        calls.append(guard_result.normalized_sql)
        if len(calls) == 1:
            raise CatalogException("Catalog Error: Column does not exist")
        return QueryResult(
            columns=["order_id"],
            rows=[["O1"]],
            row_count=1,
        )

    events = _parse_events(
        iter_chat_events(
            "查询订单",
            provider=RepairProvider(),
            schema_context_builder=lambda: "# Schema Context",
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    assert [event["data"].get("step") for event in events[:-1]] == [
        "datasource_selected",
        "intent_guard",
        "build_context",
        "olap_detected",
        "generate_sql",
        "sql_guard",
        "repair_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]
    repair_step = next(event for event in events if event["data"].get("step") == "repair_sql")
    assert repair_step["data"]["error_stage"] == "execute"
    assert repair_step["data"]["error_kind"] == "CatalogException"
    assert events[-1]["data"]["result"]["columns"] == ["order_id"]
    assert events[-1]["data"]["repair_history"][0]["succeeded"] is True
    assert events[-1]["data"]["repair_history"][0]["final_stage"] == "execute"


def test_iter_chat_events_returns_failure_event_for_sql_generation_timeout():
    class TimeoutProvider:
        name = "timeout"

        def generate_sql(self, request):
            raise httpx.ReadTimeout("The read operation timed out")

    events = _parse_events(
        iter_chat_events(
            "按类目统计销售额",
            provider=TimeoutProvider(),
            schema_context_builder=lambda: "# Schema Context",
            scope_builder=_scope,
        )
    )

    assert [event["event"] for event in events] == ["step", "step", "step", "step", "error"]
    assert events[-1]["data"] == {
        "step": "generate_sql",
        "reason": "SQL generation timed out.",
        "error_kind": "failure",
    }


def test_chat_query_endpoint_is_registered(monkeypatch):
    def fake_iter_chat_events(question, datasource_name):
        assert question == "hello"
        assert datasource_name == "duckdb_ecommerce"
        yield "event: done\ndata: {\"ok\": true}\n\n"

    monkeypatch.setattr("backend.app.api.chat.iter_chat_events", fake_iter_chat_events)
    client = TestClient(main.app)

    response = client.post("/api/chat/query", json={"question": "hello"})

    assert response.status_code == 200
    assert "event: done" in response.text


def test_chat_query_endpoint_passes_requested_datasource(monkeypatch):
    def fake_iter_chat_events(question, datasource_name):
        assert question == "hello"
        assert datasource_name == "clickhouse_ecommerce"
        yield "event: done\ndata: {\"ok\": true}\n\n"

    monkeypatch.setattr("backend.app.api.chat.iter_chat_events", fake_iter_chat_events)
    client = TestClient(main.app)

    response = client.post(
        "/api/chat/query",
        json={"question": "hello", "datasource": "clickhouse_ecommerce"},
    )

    assert response.status_code == 200
    assert "event: done" in response.text


def test_datasources_endpoint_lists_available_sources(monkeypatch):
    class FakeManager:
        default_name = "duckdb_ecommerce"

        def list_sources(self):
            return [
                DataSourceInfo(
                    name="duckdb_ecommerce",
                    dialect="duckdb",
                    display_name="DuckDB (本地)",
                ),
                DataSourceInfo(
                    name="clickhouse_ecommerce",
                    dialect="clickhouse",
                    display_name="ClickHouse (OLAP)",
                ),
            ]

    monkeypatch.setattr("backend.app.api.datasources.get_datasource_manager", lambda: FakeManager())
    client = TestClient(main.app)

    response = client.get("/api/datasources")

    assert response.status_code == 200
    assert response.json() == {
        "sources": [
            {
                "name": "duckdb_ecommerce",
                "dialect": "duckdb",
                "display_name": "DuckDB (本地)",
                "status": "available",
            },
            {
                "name": "clickhouse_ecommerce",
                "dialect": "clickhouse",
                "display_name": "ClickHouse (OLAP)",
                "status": "available",
            },
        ],
        "default": "duckdb_ecommerce",
    }


def test_health_endpoint_reports_configured_llm_provider(monkeypatch):
    monkeypatch.setattr(
        "backend.app.main.get_settings",
        lambda: SimpleNamespace(llm_provider="deepseek"),
    )
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "llm_provider": "deepseek"}

    api_response = client.get("/api/health")
    assert api_response.status_code == 200
    assert api_response.json() == {"status": "ok", "llm_provider": "deepseek"}


def test_api_allows_local_vite_origin():
    client = TestClient(main.app)

    response = client.get(
        "/api/datasources",
        headers={"Origin": "http://127.0.0.1:5175"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5175"


def test_iter_chat_events_uses_configured_deepseek_provider(monkeypatch):
    class FakeDeepSeekProvider:
        name = "deepseek"

        def generate_sql(self, request):
            assert request.schema_context == "# Schema Context"
            return SQLGenerationResult(
                sql="SELECT order_id, payment_amount FROM fact_orders",
                provider=self.name,
            )

    monkeypatch.setattr(
        "backend.app.api.chat.get_settings",
        lambda: SimpleNamespace(llm_provider="deepseek"),
    )
    monkeypatch.setattr("backend.app.api.chat.DeepSeekProvider", FakeDeepSeekProvider)

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        return QueryResult(
            columns=["order_id", "payment_amount"],
            rows=[["O00000001", 100]],
            row_count=1,
        )

    events = _parse_events(
        iter_chat_events(
            "查询订单金额",
            schema_context_builder=lambda: "# Schema Context",
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    generate_step = next(event for event in events if event["data"].get("step") == "generate_sql")
    assert generate_step["data"]["provider"] == "deepseek"


def _parse_events(chunks) -> list[dict]:
    events = []
    for chunk in chunks:
        lines = [line for line in chunk.strip().splitlines() if line]
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append({"event": event, "data": data})
    return events


def _scope(datasource_name: str = "duckdb_ecommerce") -> GuardScope:
    del datasource_name
    return GuardScope(
        allowed_tables=frozenset({"fact_orders", "dim_date"}),
        table_columns={
            "fact_orders": frozenset({"order_id", "date_key", "payment_amount"}),
            "dim_date": frozenset({"date_key", "date_value"}),
        },
    )
