from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator

from backend.app.agent.conversation import (
    conversation_context_prompt,
    merge_prior_assets_into_retrieval,
)
from backend.app.agent.explainability import build_query_explainability
from backend.app.agent.olap_intent import build_olap_hint, detect_olap_intents
from backend.app.agent.state import AgentState
from backend.app.agent.sql_postprocess import normalize_generated_sql
from backend.app.config import get_settings, retrieval_fallback_mode, retrieval_recovery_enabled
from backend.app.connectors.registry import get_datasource_manager
from backend.app.core.llm_provider import (
    LLMProvider,
    SQLGenerationRequest,
    SQLRepairContext,
)
from backend.app.execution.runner import QueryResult, execute_guarded_sql
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.metadata.retrieval_coverage import (
    expand_via_graph,
    full_schema_fits_budget,
    is_empty_retrieval,
    score_coverage,
)
from backend.app.metadata.service import build_focused_context, build_focused_context_from_retrieval, build_schema_context
from backend.app.sql_guard.guard import guard_sql
from backend.app.sql_guard.scope import GuardScope, build_default_guard_scope


SchemaContextBuilder = Callable[[], str]
Retriever = Callable[..., dict]
ScopeBuilder = Callable[..., GuardScope]
SQLExecutor = Callable[..., QueryResult]
logger = logging.getLogger(__name__)


_SQL_COMMAND_INTENT_PATTERNS = (
    ("DELETE", re.compile(r"^\s*delete\s+from\b", re.IGNORECASE)),
    ("UPDATE", re.compile(r"^\s*update\s+[A-Za-z_][A-Za-z0-9_.]*\b", re.IGNORECASE)),
    ("INSERT", re.compile(r"^\s*insert\s+into\b", re.IGNORECASE)),
    (
        "CREATE",
        re.compile(
            r"^\s*create\s+(?:or\s+replace\s+)?(?:table|view|schema|database|index)\b",
            re.IGNORECASE,
        ),
    ),
    ("DROP", re.compile(r"^\s*drop\s+(?:table\s+)?[A-Za-z_][A-Za-z0-9_.]*\b", re.IGNORECASE)),
    ("ALTER", re.compile(r"^\s*alter\s+(?:table|view|schema|database)\b", re.IGNORECASE)),
    ("TRUNCATE", re.compile(r"^\s*truncate\s+(?:table\s+)?[A-Za-z_][A-Za-z0-9_.]*\b", re.IGNORECASE)),
    (
        "COPY/LOAD",
        re.compile(
            (
                r"^\s*(?:copy\s+(?:\([^)]+\)|[A-Za-z_][A-Za-z0-9_.]*)\s+(?:from|to)\b|"
                r"(?:load|install)\s+(?:extension\s+)?"
                r"(?:httpfs|sqlite|postgres|mysql|json|spatial|parquet|iceberg|delta)\b|"
                r"(?:load|install)\s+extension\b)"
            ),
            re.IGNORECASE,
        ),
    ),
)
_ENGLISH_MUTATION_VERBS: dict[str, set[str]] = {
    "DELETE": {"delete", "remove", "clear", "truncate"},
    "UPDATE": {"update", "modify", "change"},
    "INSERT": {"insert", "add", "write"},
    "CREATE": {"create"},
    "DROP": {"drop"},
    "ALTER": {"alter"},
}
_ENGLISH_DATA_OBJECT_TOKENS = {
    "data",
    "dataset",
    "database",
    "schema",
    "table",
    "tables",
    "view",
    "views",
    "index",
    "indexes",
    "row",
    "rows",
    "record",
    "records",
    "column",
    "columns",
    "order",
    "orders",
}
_ENGLISH_ADMIN_OBJECT_TOKENS = {
    "extension",
    "extensions",
    "httpfs",
}
_CHINESE_DELETE_VERB = r"(?<!已)(?<!被)(?:删除(?!率|的|过的)|删掉(?!的|过的)|清空|清除)"
_CHINESE_MUTATION_PATTERNS = (
    (
        "DELETE",
        (
            re.compile(_CHINESE_DELETE_VERB + r".{0,12}(?:数据|订单|记录|行)"),
            re.compile(r"(?:数据|订单|记录|行).{0,12}" + _CHINESE_DELETE_VERB),
        ),
    ),
    (
        "UPDATE",
        (
            re.compile(r"(?:更新|修改).{0,12}(?:数据|订单|记录|表|字段|列|状态)"),
            re.compile(r"(?:数据|订单|记录|表|字段|列|状态).{0,12}(?:更新|修改)"),
        ),
    ),
    (
        "INSERT",
        (
            re.compile(r"(?:新增|插入|写入).{0,12}(?:数据|订单|记录|表|行)"),
            re.compile(r"(?:数据|订单|记录|表|行).{0,12}(?:新增|插入|写入)"),
        ),
    ),
    (
        "CREATE",
        (
            re.compile(r"(?:创建|新建|建).{0,8}(?:表|数据表|数据库|视图|索引)"),
            re.compile(r"(?:建表|创建表|新建表|建库|创建数据库|新建数据库)"),
        ),
    ),
    (
        "DROP",
        (
            re.compile(r"(?:删除|删掉|移除).{0,8}(?:表|数据库|视图|索引)"),
            re.compile(r"(?:删表|删除表|删库|删除数据库)"),
        ),
    ),
    (
        "ALTER",
        (
            re.compile(r"(?:修改|变更|调整).{0,8}(?:表结构|字段|列|schema|模式)"),
        ),
    ),
)
_EXTERNAL_FUNCTION_RE = re.compile(
    r"\b(?:read_csv|read_json|read_parquet|s3|url|hdfs|remote|remoteSecure)\s*\(",
    re.IGNORECASE,
)
_EXTERNAL_FILE_PATTERNS = (
    re.compile(
        r"\b(?:read|load|import|ingest)\b.{0,40}\b(?:csv|json|parquet|file|files|s3|https?|url|path)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:csv|json|parquet|file|files|s3|https?|url|path)\b.{0,40}\b(?:read|load|import|ingest)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:读取|导入|加载).{0,24}(?:外部|文件|csv|json|parquet|路径|本地|远程|s3|url|http)", re.IGNORECASE),
    re.compile(r"(?:外部|文件|csv|json|parquet|路径|本地|远程|s3|url|http).{0,24}(?:读取|导入|加载)", re.IGNORECASE),
)


def iter_pre_repair_workflow(
    state: AgentState,
    provider: LLMProvider,
    schema_context_builder: SchemaContextBuilder | None = None,
    retriever: Retriever = retrieve_metadata_assets,
) -> Iterator[str]:
    datasource_selected_node(state)
    yield "datasource_selected"
    if state.stopped_at is not None:
        return

    intent_guard_node(state)
    yield "intent_guard"
    if state.stopped_at is not None:
        return

    if schema_context_builder is None:
        retrieve_context_node(state, retriever=retriever)
        yield "retrieve_context"

    build_context_node(state, schema_context_builder=schema_context_builder)
    yield "build_context"

    olap_intent_detect_node(state)
    yield "olap_detected"

    try:
        generate_sql_node(state, provider=provider)
    except Exception as exc:
        state.stopped_at = "generate_sql"
        state.error = str(exc)
        raise
    yield "generate_sql"


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
        retrieval_result = merge_prior_assets_into_retrieval(
            state.retrieval_result,
            state.conversation_context,
        )
        settings = get_settings()
        if retrieval_recovery_enabled(settings):
            retrieval_result["retrieval_stage"] = "merged"
            coverage_olap_intents = state.olap_intents or list(detect_olap_intents(state.question))
            if is_empty_retrieval(retrieval_result):
                state.schema_context = build_schema_context(datasource_name=state.datasource_name)
                coverage = score_coverage(
                    retrieval_result,
                    datasource_name=state.datasource_name,
                    olap_intents=coverage_olap_intents,
                )
                coverage.fallback_used = True
                state.retrieval_coverage = coverage.as_dict()
            else:
                coverage = score_coverage(
                    retrieval_result,
                    datasource_name=state.datasource_name,
                    olap_intents=coverage_olap_intents,
                )
                if coverage.band == "low" and bool(getattr(settings, "retrieval_expansion_enabled", False)):
                    retrieval_result["retrieval_coverage"] = coverage.as_dict()
                    retrieval_result = expand_via_graph(
                        retrieval_result,
                        datasource_name=state.datasource_name,
                    )
                    coverage = score_coverage(
                        retrieval_result,
                        datasource_name=state.datasource_name,
                        olap_intents=coverage_olap_intents,
                    )
                    coverage.expanded = bool(retrieval_result.get("retrieval_coverage", {}).get("expanded"))

                if coverage.band == "low" and retrieval_fallback_mode(settings) == "on":
                    full_context = build_schema_context(datasource_name=state.datasource_name)
                    if full_schema_fits_budget(full_context):
                        coverage.fallback_used = True
                        state.retrieval_coverage = coverage.as_dict()
                        state.schema_context = full_context
                    else:
                        logger.info(
                            "Skipping retrieval full-schema fallback because context exceeds budget.",
                            extra={"datasource": state.datasource_name, "coverage": coverage.as_dict()},
                        )
                        state.retrieval_coverage = coverage.as_dict()
                        state.schema_context = build_focused_context_from_retrieval(
                            retrieval_result,
                            datasource_name=state.datasource_name,
                            expand_join_partners=not coverage.expanded,
                        )
                else:
                    state.retrieval_coverage = coverage.as_dict()
                    state.schema_context = build_focused_context_from_retrieval(
                        retrieval_result,
                        datasource_name=state.datasource_name,
                        expand_join_partners=not coverage.expanded,
                    )
            state.retrieval_result = retrieval_result
        else:
            state.schema_context = build_focused_context_from_retrieval(
                retrieval_result,
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
    state.olap_intents = list(detect_olap_intents(state.question))
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

    default_ranking_limit, default_browse_limit = _sql_generation_defaults()
    result = provider.generate_sql(
        SQLGenerationRequest(
            question=state.question,
            schema_context=state.schema_context,
            datasource_name=state.datasource_name,
            datasource_dialect=state.datasource_dialect,
            olap_intents=state.olap_intents,
            olap_hint=state.olap_hint,
            prior_sql=state.conversation_context.normalized_sql if state.conversation_context else None,
            prior_summary=conversation_context_prompt(state.conversation_context) if state.conversation_context else None,
            carried_filters=list(state.conversation_context.active_filters) if state.conversation_context else [],
            default_ranking_limit=default_ranking_limit,
            default_browse_limit=default_browse_limit,
        )
    )
    state.sql = normalize_generated_sql(result.sql)
    state.provider = result.provider
    state.matched_query_id = result.matched_query_id
    state.is_follow_up = result.is_follow_up
    state.change_kind = result.change_kind
    state.completed_steps.append("generate_sql")
    return state


def repair_sql_node(
    state: AgentState,
    provider: LLMProvider,
    repair_context: SQLRepairContext,
) -> AgentState:
    if state.schema_context is None:
        raise ValueError("schema_context is required before SQL repair.")

    default_ranking_limit, default_browse_limit = _sql_generation_defaults()
    result = provider.generate_sql(
        SQLGenerationRequest(
            question=state.question,
            schema_context=state.schema_context,
            repair=repair_context,
            datasource_name=state.datasource_name,
            datasource_dialect=state.datasource_dialect,
            olap_intents=state.olap_intents,
            olap_hint=state.olap_hint,
            prior_sql=state.conversation_context.normalized_sql if state.conversation_context else None,
            prior_summary=conversation_context_prompt(state.conversation_context) if state.conversation_context else None,
            carried_filters=list(state.conversation_context.active_filters) if state.conversation_context else [],
            default_ranking_limit=default_ranking_limit,
            default_browse_limit=default_browse_limit,
        )
    )
    repaired_sql = result.sql
    state.provider = result.provider
    state.matched_query_id = result.matched_query_id
    if result.is_follow_up:
        state.is_follow_up = result.is_follow_up
    if result.change_kind != "none":
        state.change_kind = result.change_kind
    state.sql = normalize_generated_sql(repaired_sql)
    state.repair_history.append(
        {
            "attempt": repair_context.attempt,
            "original_sql": repair_context.original_sql,
            "repaired_sql": repaired_sql,
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


def _sql_generation_defaults() -> tuple[int, int]:
    # get_settings() is lru-cached; keep config lookup centralized for generation and repair paths.
    settings = get_settings()
    return settings.sql_default_ranking_limit, settings.sql_default_browse_limit


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
    if _matches_external_file_intent(question):
        return "EXTERNAL_FILE_READ"

    for operation, pattern in _SQL_COMMAND_INTENT_PATTERNS:
        if pattern.search(question):
            return operation

    for operation, patterns in _CHINESE_MUTATION_PATTERNS:
        if any(pattern.search(question) for pattern in patterns):
            return operation

    tokens = set(_ascii_tokens(question))
    table_like_tokens = {token for token in tokens if token.startswith(("fact_", "dim_"))}
    object_tokens = tokens & _ENGLISH_DATA_OBJECT_TOKENS
    for operation, verbs in _ENGLISH_MUTATION_VERBS.items():
        if not tokens & verbs:
            continue
        if operation == "CREATE" and not object_tokens & {"database", "schema", "table", "tables", "view", "views", "index", "indexes"}:
            continue
        # Natural-language fallback for write intent that is not written as SQL,
        # such as "delete fact_orders" or "remove rows from fact_orders".
        if object_tokens or table_like_tokens:
            return operation

    # Safety net for extension management phrased as natural language.
    if tokens & {"install", "load"} and tokens & _ENGLISH_ADMIN_OBJECT_TOKENS:
        return "COPY/LOAD"
    return None


def _matches_external_file_intent(question: str) -> bool:
    return bool(_EXTERNAL_FUNCTION_RE.search(question)) or any(
        pattern.search(question) for pattern in _EXTERNAL_FILE_PATTERNS
    )


def _ascii_tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.casefold()))
