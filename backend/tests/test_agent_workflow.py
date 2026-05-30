import pytest

import backend.app.agent.nodes as nodes_module
from backend.app.agent.nodes import (
    build_context_node,
    execute_node,
    generate_sql_node,
    intent_guard_node,
    retrieve_context_node,
    run_query_workflow,
    sql_guard_node,
)
from backend.app.agent.state import AgentState
from backend.app.core.llm_provider import MockLLMProvider, SQLGenerationRequest, SQLGenerationResult
from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult
from backend.app.sql_guard.scope import GuardScope


def test_retrieve_context_node_sets_result_and_step():
    state = AgentState(question="test")

    retrieve_context_node(state, retriever=lambda question: {"question": question, "tables": []})

    assert state.retrieval_result == {"question": "test", "tables": []}
    assert state.completed_steps == ["retrieve_context"]


@pytest.mark.parametrize(
    ("question", "expected_error"),
    [
        ("删除2024年的订单数据", "DELETE intent is not allowed."),
        ("从外部 CSV 读取订单数据", "EXTERNAL_FILE_READ intent is not allowed."),
    ],
)
def test_intent_guard_node_rejects_blocked_questions(question: str, expected_error: str):
    state = AgentState(question=question)

    intent_guard_node(state)

    assert state.stopped_at == "intent_guard"
    assert state.error == expected_error
    assert state.completed_steps == ["intent_guard"]


def test_build_context_node_sets_context_and_step():
    state = AgentState(question="test")

    build_context_node(state, schema_context_builder=lambda: "# Context")

    assert state.schema_context == "# Context"
    assert state.completed_steps == ["build_context"]


def test_build_context_node_uses_retrieval_result(monkeypatch):
    state = AgentState(question="test", retrieval_result={"fallback_used": False})
    monkeypatch.setattr(
        nodes_module,
        "build_focused_context_from_retrieval",
        lambda retrieval_result: f"# Focused {retrieval_result['fallback_used']}",
    )

    build_context_node(state)

    assert state.schema_context == "# Focused False"
    assert state.completed_steps == ["build_context"]


def test_build_context_node_retrieves_when_missing_retrieval_result(monkeypatch):
    state = AgentState(question="test")
    monkeypatch.setattr(nodes_module, "build_focused_context", lambda question: f"# Focused {question}")

    build_context_node(state)

    assert state.schema_context == "# Focused test"
    assert state.completed_steps == ["build_context"]


def test_generate_sql_node_requires_schema_context():
    state = AgentState(question="test")

    with pytest.raises(ValueError, match="schema_context"):
        generate_sql_node(state, provider=MockLLMProvider())


def test_sql_guard_node_requires_sql():
    state = AgentState(question="test")

    with pytest.raises(ValueError, match="sql is required"):
        sql_guard_node(state, scope_builder=_scope)


def test_execute_node_requires_guard_result():
    state = AgentState(question="test")

    with pytest.raises(ValueError, match="guard_result"):
        execute_node(state)


def test_execute_node_skips_rejected_guard_result_without_overwriting_error():
    state = AgentState(
        question="test",
        guard_result=GuardResult(allowed=False, stage="operation_guard", reason="DROP is not allowed."),
        error="existing error",
        stopped_at="sql_guard",
    )

    execute_node(state)

    assert state.error == "existing error"
    assert state.stopped_at == "sql_guard"
    assert state.query_result is None
    assert state.completed_steps == []


def test_run_query_workflow_executes_demo_question():
    executed_sql = []

    def fake_executor(guard_result: GuardResult) -> QueryResult:
        executed_sql.append(guard_result.normalized_sql)
        return QueryResult(
            columns=["date_value", "sales_amount", "order_count"],
            rows=[["2025-12-31", 100, 2]],
            row_count=1,
        )

    state = run_query_workflow(
        "查询最近30天每日销售额和订单数",
        schema_context_builder=lambda: "# Schema Context",
        scope_builder=_scope,
        executor=fake_executor,
    )

    assert state.stopped_at is None
    assert state.error is None
    assert state.provider == "mock"
    assert state.matched_query_id == "recent_30d_daily_sales"
    assert state.guard_result is not None
    assert state.guard_result.allowed is True
    assert state.explainability is not None
    assert state.explainability["matched_tables"] == ["dim_date", "fact_orders"]
    assert "fact_orders.payment_amount" in state.explainability["matched_columns"]
    assert state.explainability["date_interpretation"]["matched"] is True
    assert state.explainability["guard_result"]["allowed"] is True
    assert state.query_result is not None
    assert state.query_result.row_count == 1
    assert state.summary == "查询返回 1 行，字段：date_value, sales_amount, order_count。"
    assert state.completed_steps == [
        "intent_guard",
        "build_context",
        "generate_sql",
        "sql_guard",
        "execute",
        "summarize",
    ]
    assert executed_sql
    assert "LIMIT 500" in executed_sql[0]


def test_run_query_workflow_default_uses_retrieval_and_focused_context(monkeypatch):
    captured_schema_context = []

    class CapturingProvider:
        name = "capturing"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            captured_schema_context.append(request.schema_context)
            return SQLGenerationResult(
                sql="SELECT order_id FROM fact_orders",
                provider=self.name,
            )

    def fake_executor(guard_result: GuardResult) -> QueryResult:
        return QueryResult(columns=["order_id"], rows=[["O1"]], row_count=1)

    monkeypatch.setattr(
        nodes_module,
        "build_focused_context_from_retrieval",
        lambda retrieval_result: "# Focused Context",
    )

    state = run_query_workflow(
        "查询订单",
        provider=CapturingProvider(),
        retriever=lambda question: {"question": question, "fallback_used": False},
        scope_builder=_scope,
        executor=fake_executor,
    )

    assert state.retrieval_result == {"question": "查询订单", "fallback_used": False}
    assert captured_schema_context == ["# Focused Context"]
    assert state.completed_steps == [
        "intent_guard",
        "retrieve_context",
        "build_context",
        "generate_sql",
        "sql_guard",
        "execute",
        "summarize",
    ]


def test_run_query_workflow_stops_when_intent_guard_rejects_question():
    class FailingProvider:
        name = "failing"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            raise AssertionError("provider should not be called")

    def failing_retriever(question: str) -> dict:
        raise AssertionError("retriever should not be called")

    state = run_query_workflow(
        "删除2024年的订单数据",
        provider=FailingProvider(),
        retriever=failing_retriever,
        scope_builder=_scope,
    )

    assert state.stopped_at == "intent_guard"
    assert state.error == "DELETE intent is not allowed."
    assert state.sql is None
    assert state.guard_result is None
    assert state.query_result is None
    assert state.completed_steps == ["intent_guard"]


def test_run_query_workflow_stops_when_guard_rejects_sql():
    class DeleteProvider:
        name = "delete-provider"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            return SQLGenerationResult(sql="DELETE FROM fact_orders", provider=self.name)

    executed = []

    def fake_executor(guard_result: GuardResult) -> QueryResult:
        executed.append(guard_result)
        return QueryResult(columns=[], rows=[], row_count=0)

    state = run_query_workflow(
        "查询订单",
        provider=DeleteProvider(),
        schema_context_builder=lambda: "# Schema Context",
        scope_builder=_scope,
        executor=fake_executor,
    )

    assert state.stopped_at == "sql_guard"
    assert state.error == "DELETE is not allowed."
    assert state.guard_result is not None
    assert state.guard_result.allowed is False
    assert state.explainability is not None
    assert state.explainability["guard_result"]["allowed"] is False
    assert state.explainability["guard_result"]["stage"] == "operation_guard"
    assert state.query_result is None
    assert executed == []
    assert state.completed_steps == ["intent_guard", "build_context", "generate_sql", "sql_guard"]


def _scope() -> GuardScope:
    return GuardScope(
        allowed_tables=frozenset({"fact_orders", "dim_date"}),
        table_columns={
            "fact_orders": frozenset({"order_id", "date_key", "payment_amount"}),
            "dim_date": frozenset({"date_key", "date_value"}),
        },
    )
