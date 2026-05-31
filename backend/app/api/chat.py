import json
import logging
from collections.abc import Iterator

import httpx
from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.agent.nodes import (
    build_context_node,
    execute_node,
    generate_sql_node,
    intent_guard_node,
    retrieve_context_node,
    sql_guard_node,
    summarize_node,
)
from backend.app.agent.state import AgentState
from backend.app.config import get_settings
from backend.app.core.deepseek_provider import DeepSeekProvider
from backend.app.core.llm_provider import LLMProvider, MockLLMProvider
from backend.app.execution.runner import QueryResult, execute_guarded_sql
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.sql_guard.scope import GuardScope, build_default_guard_scope
from backend.app.visualization.recommender import recommend_chart

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


class ChatQueryRequest(BaseModel):
    question: str


@router.post("/query")
def query_endpoint(request: ChatQueryRequest) -> StreamingResponse:
    return StreamingResponse(
        iter_chat_events(request.question),
        media_type="text/event-stream",
    )


def iter_chat_events(
    question: str,
    provider: LLMProvider | None = None,
    schema_context_builder=None,
    retriever=retrieve_metadata_assets,
    scope_builder=build_default_guard_scope,
    executor=execute_guarded_sql,
) -> Iterator[str]:
    state = AgentState(question=question)
    try:
        intent_guard_node(state)
        if state.stopped_at is not None:
            yield _sse_event(
                "error",
                {
                    "step": state.stopped_at,
                    "reason": state.error,
                    "error_kind": "blocked",
                },
            )
            return
        yield _sse_event("step", {"step": "intent_guard", "status": "completed"})

        if schema_context_builder is None:
            retrieve_context_node(state, retriever=retriever)
            yield _sse_event("step", _retrieval_step_payload(state.retrieval_result))

        build_context_node(state, schema_context_builder=schema_context_builder)
        yield _sse_event("step", {"step": "build_context", "status": "completed"})

        generate_sql_node(state, provider=provider or get_default_llm_provider())
        yield _sse_event(
            "step",
            {
                "step": "generate_sql",
                "status": "completed",
                "provider": state.provider,
                "sql": state.sql,
                "matched_query_id": state.matched_query_id,
            },
        )

        sql_guard_node(state, scope_builder=scope_builder)
        yield _sse_event(
            "step",
            {
                "step": "sql_guard",
                "status": "completed",
                "guard_result": _model_dump(state.guard_result),
            },
        )
        if state.stopped_at is not None:
            yield _sse_event(
                "error",
                {
                    "step": state.stopped_at,
                    "reason": state.error,
                    "error_kind": "blocked",
                    "explainability": state.explainability,
                },
            )
            return

        execute_node(state, executor=executor)
        yield _sse_event(
            "step",
            {
                "step": "execute",
                "status": "completed",
                "columns": state.query_result.columns if state.query_result else [],
                "row_count": state.query_result.row_count if state.query_result else 0,
            },
        )

        summarize_node(state)
        yield _sse_event(
            "step",
            {
                "step": "summarize",
                "status": "completed",
                "summary": state.summary,
            },
        )

        chart_recommendation = recommend_chart(state.query_result or QueryResult(columns=[], rows=[], row_count=0))
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
            },
        )
    except httpx.ReadTimeout:
        logger.exception("Chat query timed out during SQL generation")
        yield _sse_event(
            "error",
            {
                "step": "generate_sql",
                "reason": "SQL generation timed out.",
                "error_kind": "failure",
            },
        )
    except Exception as exc:
        logger.exception("Chat query failed")
        yield _sse_event(
            "error",
            {
                "step": state.completed_steps[-1] if state.completed_steps else "unknown",
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
