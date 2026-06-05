from __future__ import annotations

from backend.app.agent.nodes import (
    Retriever,
    SchemaContextBuilder,
    ScopeBuilder,
    SQLExecutor,
    iter_pre_repair_workflow,
    summarize_node,
)
from backend.app.agent.performance import explain_performance_node
from backend.app.agent.repair import iter_sql_repair_events
from backend.app.agent.state import AgentState
from backend.app.agent.conversation import ConversationContext
from backend.app.core.llm_provider import LLMProvider, MockLLMProvider
from backend.app.execution.runner import execute_guarded_sql
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.sql_guard.scope import build_default_guard_scope
from backend.app.visualization.recommender import recommend_chart


def finalize_workflow(state: AgentState) -> AgentState:
    """Post-execution stages shared by every workflow entrypoint.

    Computes EXPLAIN performance hints, the result summary, and the chart
    recommendation, storing each on ``state``. Only valid once the query has
    executed successfully (``state.query_result`` is set); both the streaming
    (``api.chat.iter_chat_events``) and synchronous (``run_query_workflow``)
    paths call this so they cannot diverge.
    """
    explain_performance_node(state)
    summarize_node(state)
    state.chart_recommendation = recommend_chart(
        state.query_result,
        olap_intents=state.olap_intents,
    )
    state.completed_steps.append("recommend_chart")
    return state


def run_query_workflow(
    question: str,
    provider: LLMProvider | None = None,
    schema_context_builder: SchemaContextBuilder | None = None,
    retriever: Retriever = retrieve_metadata_assets,
    scope_builder: ScopeBuilder = build_default_guard_scope,
    executor: SQLExecutor = execute_guarded_sql,
    datasource_name: str = DEFAULT_DATASOURCE,
    conversation_context: ConversationContext | None = None,
) -> AgentState:
    """Synchronous workflow driver.

    Drives the same generators as the streaming SSE path (pre-repair stages,
    the SQL-Guard/repair loop, then ``finalize_workflow``) so unit tests and
    production exercise one pipeline.
    """
    state = AgentState(
        question=question,
        datasource_name=datasource_name,
        conversation_context=conversation_context
        if conversation_context is None or conversation_context.datasource_name == datasource_name
        else None,
    )
    active_provider = provider or MockLLMProvider()

    for _step in iter_pre_repair_workflow(
        state,
        provider=active_provider,
        schema_context_builder=schema_context_builder,
        retriever=retriever,
    ):
        if state.stopped_at is not None:
            return state

    last_event = None
    for last_event in iter_sql_repair_events(
        state,
        provider=active_provider,
        scope_builder=scope_builder,
        executor=executor,
    ):
        pass
    if last_event is not None and last_event.step == "error":
        return state

    finalize_workflow(state)
    return state
