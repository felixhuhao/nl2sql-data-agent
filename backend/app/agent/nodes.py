from __future__ import annotations

from collections.abc import Callable

from backend.app.agent.explainability import build_query_explainability
from backend.app.agent.olap_intent import build_olap_hint, detect_olap_intents
from backend.app.agent.state import AgentState
from backend.app.agent.sql_postprocess import normalize_generated_sql
from backend.app.connectors.registry import get_datasource_manager
from backend.app.core.llm_provider import (
    LLMProvider,
    MockLLMProvider,
    SQLGenerationRequest,
    SQLRepairContext,
)
from backend.app.execution.runner import QueryResult, execute_guarded_sql
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.metadata.service import build_focused_context, build_focused_context_from_retrieval
from backend.app.sql_guard.guard import guard_sql
from backend.app.sql_guard.scope import GuardScope, build_default_guard_scope


SchemaContextBuilder = Callable[[], str]
Retriever = Callable[..., dict]
ScopeBuilder = Callable[..., GuardScope]
SQLExecutor = Callable[..., QueryResult]


_BLOCKED_INTENT_KEYWORDS = (
    ("DELETE", ("delete", "删除", "删掉", "清空", "清除")),
    ("UPDATE", ("update", "修改", "更新", "改为", "改成")),
    ("INSERT", ("insert", "新增", "插入", "写入")),
    ("CREATE", ("create", "建表", "创建表", "创建一张表")),
    ("DROP", ("drop", "删表")),
    ("ALTER", ("alter", "改表", "修改表结构")),
    ("TRUNCATE", ("truncate", "截断")),
    ("COPY/LOAD", ("copy", "load", "install", "导入", "加载")),
)
_EXTERNAL_FILE_KEYWORDS = (
    "read_csv",
    "read_json",
    "read_parquet",
    "外部csv",
    "外部parquet",
    "外部json",
    "外部文件",
)


def run_query_workflow(
    question: str,
    provider: LLMProvider | None = None,
    schema_context_builder: SchemaContextBuilder | None = None,
    retriever: Retriever = retrieve_metadata_assets,
    scope_builder: ScopeBuilder = build_default_guard_scope,
    executor: SQLExecutor = execute_guarded_sql,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> AgentState:
    state = AgentState(question=question, datasource_name=datasource_name)
    datasource_selected_node(state)
    if state.stopped_at is not None:
        return state
    intent_guard_node(state)
    if state.stopped_at is not None:
        return state
    if schema_context_builder is None:
        retrieve_context_node(state, retriever=retriever)
    build_context_node(state, schema_context_builder=schema_context_builder)
    olap_intent_detect_node(state)
    generate_sql_node(state, provider=provider or MockLLMProvider())
    sql_guard_node(state, scope_builder=scope_builder)
    if state.stopped_at is not None:
        return state
    execute_node(state, executor=executor)
    summarize_node(state)
    return state


def datasource_selected_node(state: AgentState) -> AgentState:
    try:
        connector = get_datasource_manager().get(state.datasource_name)
    except (KeyError, LookupError) as exc:
        state.error = str(exc)
        state.stopped_at = "datasource_selected"
    else:
        state.datasource_name = connector.name
        state.datasource_dialect = connector.dialect
        state.datasource_display_name = connector.display_name
    state.completed_steps.append("datasource_selected")
    return state


def intent_guard_node(state: AgentState) -> AgentState:
    blocked_intent = _detect_blocked_intent(state.question)
    state.completed_steps.append("intent_guard")
    if blocked_intent is not None:
        state.error = f"{blocked_intent} intent is not allowed."
        state.stopped_at = "intent_guard"
    return state


def retrieve_context_node(
    state: AgentState,
    retriever: Retriever = retrieve_metadata_assets,
) -> AgentState:
    state.retrieval_result = retriever(state.question, datasource_name=state.datasource_name)
    state.completed_steps.append("retrieve_context")
    return state


def build_context_node(
    state: AgentState,
    schema_context_builder: SchemaContextBuilder | None = None,
) -> AgentState:
    if schema_context_builder is not None:
        state.schema_context = schema_context_builder()
    elif state.retrieval_result is not None:
        state.schema_context = build_focused_context_from_retrieval(
            state.retrieval_result,
            datasource_name=state.datasource_name,
        )
    else:
        state.schema_context = build_focused_context(state.question, datasource_name=state.datasource_name)
    state.completed_steps.append("build_context")
    return state


def olap_intent_detect_node(state: AgentState) -> AgentState:
    metrics = []
    if state.retrieval_result is not None:
        metrics = state.retrieval_result.get("metrics", [])
    state.olap_intents = detect_olap_intents(state.question)
    state.olap_hint = build_olap_hint(
        state.olap_intents,
        datasource_dialect=state.datasource_dialect,
        matched_metrics=metrics,
    )
    state.completed_steps.append("olap_detected")
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
            datasource_name=state.datasource_name,
            datasource_dialect=state.datasource_dialect,
            olap_intents=state.olap_intents,
            olap_hint=state.olap_hint,
        )
    )
    state.sql = normalize_generated_sql(result.sql)
    state.provider = result.provider
    state.matched_query_id = result.matched_query_id
    state.completed_steps.append("generate_sql")
    return state


def repair_sql_node(
    state: AgentState,
    provider: LLMProvider,
    repair_context: SQLRepairContext,
) -> AgentState:
    if state.schema_context is None:
        raise ValueError("schema_context is required before SQL repair.")

    result = provider.generate_sql(
        SQLGenerationRequest(
            question=state.question,
            schema_context=state.schema_context,
            repair=repair_context,
            datasource_name=state.datasource_name,
            datasource_dialect=state.datasource_dialect,
            olap_intents=state.olap_intents,
            olap_hint=state.olap_hint,
        )
    )
    state.sql = normalize_generated_sql(result.sql)
    state.provider = result.provider
    state.matched_query_id = result.matched_query_id
    state.repair_history.append(
        {
            "attempt": repair_context.attempt,
            "original_sql": repair_context.original_sql,
            "repaired_sql": result.sql,
            "error_stage": repair_context.error_stage,
            "error_kind": repair_context.error_kind,
            "error_reason": repair_context.error_reason,
            "normalized_sql": repair_context.normalized_sql,
            "succeeded": None,
            "final_stage": None,
        }
    )
    state.completed_steps.append("repair_sql")
    return state


def sql_guard_node(
    state: AgentState,
    scope_builder: ScopeBuilder = build_default_guard_scope,
) -> AgentState:
    if state.sql is None:
        raise ValueError("sql is required before SQL Guard.")

    state.guard_result = guard_sql(
        state.sql,
        scope=scope_builder(datasource_name=state.datasource_name),
        datasource_name=state.datasource_name,
    )
    state.explainability = build_query_explainability(
        sql=state.guard_result.normalized_sql or state.sql,
        question=state.question,
        guard_result=state.guard_result,
        datasource_name=state.datasource_name,
        datasource_dialect=state.datasource_dialect,
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

    state.query_result = executor(state.guard_result, datasource_name=state.datasource_name)
    state.completed_steps.append("execute")
    return state


def summarize_node(state: AgentState) -> AgentState:
    if state.query_result is None:
        raise ValueError("query_result is required before summarization.")

    columns = ", ".join(state.query_result.columns)
    state.summary = f"查询返回 {state.query_result.row_count} 行，字段：{columns}。"
    state.completed_steps.append("summarize")
    return state


def _detect_blocked_intent(question: str) -> str | None:
    normalized = _normalize_intent_text(question)
    if any(keyword in normalized for keyword in _EXTERNAL_FILE_KEYWORDS):
        return "EXTERNAL_FILE_READ"

    for operation, keywords in _BLOCKED_INTENT_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return operation
    return None


def _normalize_intent_text(text: str) -> str:
    return "".join(text.casefold().split())
