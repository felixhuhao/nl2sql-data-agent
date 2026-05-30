import json

from fastapi.testclient import TestClient

from backend.app import main
import backend.app.agent.nodes as nodes_module
from backend.app.api.chat import iter_chat_events
from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult
from backend.app.sql_guard.scope import GuardScope


def test_iter_chat_events_returns_step_and_done_events_for_demo_question():
    executed_sql = []

    def fake_executor(guard_result: GuardResult) -> QueryResult:
        executed_sql.append(guard_result.normalized_sql)
        return QueryResult(
            columns=["date_value", "sales_amount", "order_count"],
            rows=[["2025-12-31", 100, 2]],
            row_count=1,
        )

    events = _parse_events(
        iter_chat_events(
            "查询最近30天每日销售额和订单数",
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
        "done",
    ]
    assert [event["data"].get("step") for event in events[:-1]] == [
        "build_context",
        "generate_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]
    assert executed_sql
    assert events[-1]["data"]["result"]["row_count"] == 1
    assert events[-1]["data"]["chart_recommendation"]["chart_type"] == "line"
    assert events[-1]["data"]["explainability"]["guard_result"]["allowed"] is True


def test_iter_chat_events_returns_retrieve_context_step(monkeypatch):
    monkeypatch.setattr(
        nodes_module,
        "build_focused_context_from_retrieval",
        lambda retrieval_result: "# Focused Schema Context",
    )

    def fake_retriever(question: str) -> dict:
        assert question == "查询最近30天每日销售额和订单数"
        return {
            "question": question,
            "fallback_used": False,
            "tables": [{"table_name": "fact_orders"}],
            "columns": [{"table_name": "fact_orders", "column_name": "payment_amount"}],
            "metrics": [{"name": "sales_amount"}],
            "verified_queries": [{"id": "recent_30d_daily_sales"}],
        }

    def fake_executor(guard_result: GuardResult) -> QueryResult:
        return QueryResult(
            columns=["date_value", "sales_amount", "order_count"],
            rows=[["2025-12-31", 100, 2]],
            row_count=1,
        )

    events = _parse_events(
        iter_chat_events(
            "查询最近30天每日销售额和订单数",
            retriever=fake_retriever,
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    assert events[0] == {
        "event": "step",
        "data": {
            "step": "retrieve_context",
            "status": "completed",
            "fallback_used": False,
            "tables": ["fact_orders"],
            "columns": ["fact_orders.payment_amount"],
            "metrics": ["sales_amount"],
            "verified_queries": ["recent_30d_daily_sales"],
        },
    }
    assert [event["data"].get("step") for event in events[:-1]] == [
        "retrieve_context",
        "build_context",
        "generate_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]


def test_iter_chat_events_returns_error_event_for_guard_rejection():
    executed = []

    def fake_executor(guard_result: GuardResult) -> QueryResult:
        executed.append(guard_result)
        return QueryResult(columns=[], rows=[], row_count=0)

    events = _parse_events(
        iter_chat_events(
            "删除2024年数据",
            schema_context_builder=lambda: "# Schema Context",
            scope_builder=_scope,
            executor=fake_executor,
        )
    )

    assert [event["event"] for event in events] == ["step", "step", "step", "error"]
    assert events[-1]["data"]["step"] == "sql_guard"
    assert events[-1]["data"]["reason"] == "DELETE is not allowed."
    assert events[-1]["data"]["explainability"]["guard_result"]["allowed"] is False
    assert executed == []


def test_chat_query_endpoint_is_registered(monkeypatch):
    def fake_iter_chat_events(question):
        assert question == "hello"
        yield "event: done\ndata: {\"ok\": true}\n\n"

    monkeypatch.setattr("backend.app.api.chat.iter_chat_events", fake_iter_chat_events)
    client = TestClient(main.app)

    response = client.post("/api/chat/query", json={"question": "hello"})

    assert response.status_code == 200
    assert "event: done" in response.text


def _parse_events(chunks) -> list[dict]:
    events = []
    for chunk in chunks:
        lines = [line for line in chunk.strip().splitlines() if line]
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append({"event": event, "data": data})
    return events


def _scope() -> GuardScope:
    return GuardScope(
        allowed_tables=frozenset({"fact_orders", "dim_date"}),
        table_columns={
            "fact_orders": frozenset({"order_id", "date_key", "payment_amount"}),
            "dim_date": frozenset({"date_key", "date_value"}),
        },
    )
