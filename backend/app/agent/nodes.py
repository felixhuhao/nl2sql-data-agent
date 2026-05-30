from __future__ import annotations

from collections.abc import Callable

from backend.app.agent.state import AgentState
from backend.app.agent.explainability import build_query_explainability
from backend.app.core.llm_provider import LLMProvider, MockLLMProvider, SQLGenerationRequest
from backend.app.execution.runner import QueryResult, execute_guarded_sql
from backend.app.metadata.service import build_schema_context
from backend.app.sql_guard.guard import guard_sql
from backend.app.sql_guard.scope import GuardScope, build_default_guard_scope


SchemaContextBuilder = Callable[[], str]
ScopeBuilder = Callable[[], GuardScope]
SQLExecutor = Callable[..., QueryResult]


def run_query_workflow(
    question: str,
    provider: LLMProvider | None = None,
    schema_context_builder: SchemaContextBuilder = build_schema_context,
    scope_builder: ScopeBuilder = build_default_guard_scope,
    executor: SQLExecutor = execute_guarded_sql,
) -> AgentState:
    state = AgentState(question=question)
    build_context_node(state, schema_context_builder=schema_context_builder)
    generate_sql_node(state, provider=provider or MockLLMProvider())
    sql_guard_node(state, scope_builder=scope_builder)
    if state.stopped_at is not None:
        return state
    execute_node(state, executor=executor)
    summarize_node(state)
    return state


def build_context_node(
    state: AgentState,
    schema_context_builder: SchemaContextBuilder = build_schema_context,
) -> AgentState:
    state.schema_context = schema_context_builder()
    state.completed_steps.append("build_context")
    return state


def generate_sql_node(
    state: AgentState,
    provider: LLMProvider,
) -> AgentState:
    if state.schema_context is None:
        raise ValueError("schema_context is required before SQL generation.")

    result = provider.generate_sql(
        SQLGenerationRequest(
            question=state.question,
            schema_context=state.schema_context,
        )
    )
    state.sql = result.sql
    state.provider = result.provider
    state.matched_query_id = result.matched_query_id
    state.completed_steps.append("generate_sql")
    return state


def sql_guard_node(
    state: AgentState,
    scope_builder: ScopeBuilder = build_default_guard_scope,
) -> AgentState:
    if state.sql is None:
        raise ValueError("sql is required before SQL Guard.")

    state.guard_result = guard_sql(state.sql, scope=scope_builder())
    state.explainability = build_query_explainability(
        sql=state.guard_result.normalized_sql or state.sql,
        question=state.question,
        guard_result=state.guard_result,
    )
    state.completed_steps.append("sql_guard")
    if not state.guard_result.allowed:
        state.error = state.guard_result.reason
        state.stopped_at = "sql_guard"
    return state


def execute_node(
    state: AgentState,
    executor: SQLExecutor = execute_guarded_sql,
) -> AgentState:
    if state.guard_result is None:
        raise ValueError("guard_result is required before execution.")
    if not state.guard_result.allowed:
        return state

    state.query_result = executor(state.guard_result)
    state.completed_steps.append("execute")
    return state


def summarize_node(state: AgentState) -> AgentState:
    if state.query_result is None:
        raise ValueError("query_result is required before summarization.")

    columns = ", ".join(state.query_result.columns)
    state.summary = f"查询返回 {state.query_result.row_count} 行，字段：{columns}。"
    state.completed_steps.append("summarize")
    return state
