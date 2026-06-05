import json
import logging
from collections.abc import Iterator

import httpx
from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.agent.nodes import (
    explain_performance_node,
    iter_pre_repair_workflow,
    summarize_node,
)
from backend.app.agent.olap_intent import describe_olap_intents
from backend.app.agent.repair import RepairEvent, iter_sql_repair_events
from backend.app.agent.state import AgentState
from backend.app.config import get_settings
from backend.app.core.deepseek_provider import DeepSeekProvider
from backend.app.core.llm_provider import LLMProvider, MockLLMProvider
from backend.app.execution.runner import QueryResult, execute_guarded_sql
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.sql_guard.scope import GuardScope, build_default_guard_scope
from backend.app.visualization.recommender import recommend_chart

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


class WorkflowPayloadError(RuntimeError):
    pass


class ChatQueryRequest(BaseModel):
    question: str
    datasource: str = DEFAULT_DATASOURCE


@router.post("/query")
def query_endpoint(request: ChatQueryRequest) -> StreamingResponse:
    return StreamingResponse(
        iter_chat_events(request.question, datasource_name=request.datasource),
        media_type="text/event-stream",
    )


def iter_chat_events(
    question: str,
    datasource_name: str = DEFAULT_DATASOURCE,
    provider: LLMProvider | None = None,
    schema_context_builder=None,
    retriever=retrieve_metadata_assets,
    scope_builder=build_default_guard_scope,
    executor=execute_guarded_sql,
) -> Iterator[str]:
    state = AgentState(question=question, datasource_name=datasource_name)
    try:
        active_provider = provider or get_default_llm_provider()
        for step in iter_pre_repair_workflow(
            state,
            provider=active_provider,
            schema_context_builder=schema_context_builder,
            retriever=retriever,
        ):
            if state.stopped_at is not None:
                yield _sse_event(
                    "error",
                    {
                        "step": state.stopped_at,
                        "reason": state.error,
                        "error_kind": _pre_repair_error_kind(state.stopped_at),
                    },
                )
                return
            yield _sse_event("step", _workflow_step_payload(step, state))

        for repair_event in iter_sql_repair_events(
            state,
            provider=active_provider,
            scope_builder=scope_builder,
            executor=executor,
        ):
            if repair_event.step == "error":
                yield _sse_event("error", _repair_error_payload(repair_event))
                return
            yield _sse_event("step", _repair_step_payload(repair_event))

        explain_performance_node(state)
        if state.datasource_dialect == "clickhouse":
            yield _sse_event("step", _explain_plan_step_payload(state))

        summarize_node(state)
        yield _sse_event(
            "step",
            {
                "step": "summarize",
                "status": "completed",
                "summary": state.summary,
            },
        )

        chart_recommendation = recommend_chart(
            state.query_result or QueryResult(columns=[], rows=[], row_count=0),
            olap_intents=state.olap_intents,
        )
        yield _sse_event(
            "step",
            {
                "step": "recommend_chart",
                "status": "completed",
                "chart_recommendation": chart_recommendation.model_dump(),
            },
        )

        yield _sse_event(
            "done",
            {
                "question": state.question,
                "sql": state.sql,
                "normalized_sql": state.guard_result.normalized_sql if state.guard_result else None,
                "result": _model_dump(state.query_result),
                "summary": state.summary,
                "chart_recommendation": chart_recommendation.model_dump(),
                "explainability": state.explainability,
                "repair_history": state.repair_history,
                "datasource": _datasource_payload(state),
                "olap_intents": state.olap_intents,
                "olap_description": describe_olap_intents(state.olap_intents),
                "plan_hints": state.plan_hints,
                "runtime_stats": state.runtime_stats,
            },
        )
    except httpx.TimeoutException:
        logger.exception("Chat query timed out during SQL generation")
        yield _sse_event(
            "error",
            {
                "step": "generate_sql",
                "reason": "SQL generation timed out.",
                "error_kind": "failure",
            },
        )
    except WorkflowPayloadError:
        raise
    except Exception as exc:
        logger.exception("Chat query failed")
        yield _sse_event(
            "error",
            {
                "step": _failure_step(state),
                "reason": str(exc),
                "error_kind": "failure",
            },
        )


def get_default_llm_provider() -> LLMProvider:
    provider_name = get_settings().llm_provider.lower()
    if provider_name == "mock":
        return MockLLMProvider()
    if provider_name == "deepseek":
        return DeepSeekProvider()
    raise ValueError(f"Unsupported LLM_PROVIDER: {provider_name}")


def _sse_event(event: str, payload: dict) -> str:
    data = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _model_dump(value):
    if value is None:
        return None
    return value.model_dump()


def _retrieval_step_payload(retrieval_result: dict | None) -> dict:
    retrieval_result = retrieval_result or {}
    retrieval_meta = retrieval_result.get("retrieval_meta") or {}
    return {
        "step": "retrieve_context",
        "status": "completed",
        "fallback_used": retrieval_result.get("fallback_used", False),
        "tables": [table["table_name"] for table in retrieval_result.get("tables", [])],
        "columns": [
            f"{column['table_name']}.{column['column_name']}"
            for column in retrieval_result.get("columns", [])
        ],
        "metrics": [metric["name"] for metric in retrieval_result.get("metrics", [])],
        "verified_queries": [query["id"] for query in retrieval_result.get("verified_queries", [])],
        "vector_used": retrieval_meta.get("vector_used", False),
        "index_status": retrieval_meta.get("index_status"),
        "stale_reason": retrieval_meta.get("stale_reason"),
        "value_hits": retrieval_meta.get("value_hits", []),
        "retrieval_sources": retrieval_meta.get("sources", {}),
    }


def _datasource_step_payload(state: AgentState) -> dict:
    return {
        "step": "datasource_selected",
        "status": "completed",
        "name": state.datasource_name,
        "dialect": state.datasource_dialect,
        "display_name": state.datasource_display_name,
    }


def _datasource_payload(state: AgentState) -> dict:
    return {
        "name": state.datasource_name,
        "dialect": state.datasource_dialect,
        "display_name": state.datasource_display_name,
    }


def _workflow_step_payload(step: str, state: AgentState) -> dict:
    if step == "datasource_selected":
        return _datasource_step_payload(state)
    if step == "intent_guard":
        return {"step": "intent_guard", "status": "completed"}
    if step == "retrieve_context":
        return _retrieval_step_payload(state.retrieval_result)
    if step == "build_context":
        return {"step": "build_context", "status": "completed"}
    if step == "olap_detected":
        return _olap_step_payload(state)
    if step == "generate_sql":
        return {
            "step": "generate_sql",
            "status": "completed",
            "provider": state.provider,
            "sql": state.sql,
            "matched_query_id": state.matched_query_id,
        }
    raise WorkflowPayloadError(f"Unsupported workflow step: {step}")


def _explain_plan_step_payload(state: AgentState) -> dict:
    return {
        "step": "explain_plan",
        "status": "completed",
        "plan_hints": state.plan_hints,
        "runtime_stats": state.runtime_stats,
    }


def _failure_step(state: AgentState) -> str:
    if state.stopped_at is not None:
        return state.stopped_at
    if state.completed_steps:
        return state.completed_steps[-1]
    return "unknown"


def _pre_repair_error_kind(step: str) -> str:
    if step == "intent_guard":
        return "blocked"
    return "failure"


def _olap_step_payload(state: AgentState) -> dict:
    return {
        "step": "olap_detected",
        "status": "completed",
        "olap_intents": state.olap_intents,
        "description": describe_olap_intents(state.olap_intents),
    }


def _repair_step_payload(event: RepairEvent) -> dict:
    if event.step == "sql_guard":
        return {
            "step": "sql_guard",
            "status": "completed",
            "guard_result": _model_dump(event.state.guard_result),
        }
    if event.step == "execute":
        return {
            "step": "execute",
            "status": "completed",
            "columns": event.state.query_result.columns if event.state.query_result else [],
            "row_count": event.state.query_result.row_count if event.state.query_result else 0,
            "elapsed_ms": event.state.query_result.elapsed_ms if event.state.query_result else None,
        }
    if event.step == "repair_sql":
        latest_repair = event.state.repair_history[-1] if event.state.repair_history else {}
        return {
            "step": "repair_sql",
            "status": "completed",
            "attempt": event.attempt,
            "original_sql": latest_repair.get("original_sql"),
            "repaired_sql": latest_repair.get("repaired_sql"),
            "error_stage": latest_repair.get("error_stage"),
            "error_kind": latest_repair.get("error_kind"),
            "error_reason": latest_repair.get("error_reason"),
            "repair_history": event.state.repair_history,
        }
    raise WorkflowPayloadError(f"Unsupported repair event step: {event.step}")


def _repair_error_payload(event: RepairEvent) -> dict:
    return {
        "step": event.error_stage or event.state.stopped_at or event.step,
        "reason": event.error_reason or event.state.error or str(event.error or ""),
        "error_kind": "blocked" if event.error_stage == "sql_guard" else "failure",
        "guard_stage": event.error_kind if event.error_stage == "sql_guard" else None,
        "explainability": event.state.explainability,
        "repair_history": event.state.repair_history,
    }
