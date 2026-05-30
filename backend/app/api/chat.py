import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.agent.nodes import (
    build_context_node,
    execute_node,
    generate_sql_node,
    sql_guard_node,
    summarize_node,
)
from backend.app.agent.state import AgentState
from backend.app.core.llm_provider import LLMProvider, MockLLMProvider
from backend.app.execution.runner import QueryResult, execute_guarded_sql
from backend.app.metadata.service import build_schema_context
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
    schema_context_builder=build_schema_context,
    scope_builder=build_default_guard_scope,
    executor=execute_guarded_sql,
) -> Iterator[str]:
    state = AgentState(question=question)
    try:
        build_context_node(state, schema_context_builder=schema_context_builder)
        yield _sse_event("step", {"step": "build_context", "status": "completed"})

        generate_sql_node(state, provider=provider or MockLLMProvider())
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
    except Exception as exc:
        logger.exception("Chat query failed")
        yield _sse_event(
            "error",
            {
                "step": state.completed_steps[-1] if state.completed_steps else "unknown",
                "reason": str(exc),
            },
        )


def _sse_event(event: str, payload: dict) -> str:
    data = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _model_dump(value):
    if value is None:
        return None
    return value.model_dump()
