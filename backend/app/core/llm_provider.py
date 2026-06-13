from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.app.config import DEFAULT_BROWSE_LIMIT, DEFAULT_RANKING_LIMIT
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.service import list_verified_queries


@dataclass(frozen=True)
class SQLRepairContext:
    attempt: int
    original_sql: str
    error_stage: str
    error_kind: str
    error_reason: str
    normalized_sql: str | None = None


@dataclass(frozen=True)
class SQLGenerationRequest:
    question: str
    schema_context: str
    repair: SQLRepairContext | None = None
    datasource_name: str = DEFAULT_DATASOURCE
    datasource_dialect: str = "duckdb"
    olap_intents: list[str] = field(default_factory=list)
    olap_hint: str = ""
    prior_sql: str | None = None
    prior_summary: str | None = None
    carried_filters: list[Any] = field(default_factory=list)
    default_ranking_limit: int = DEFAULT_RANKING_LIMIT
    default_browse_limit: int = DEFAULT_BROWSE_LIMIT


@dataclass(frozen=True)
class SQLGenerationResult:
    sql: str
    provider: str
    matched_query_id: str | None = None
    is_follow_up: bool = False
    change_kind: str = "none"


class LLMProvider(Protocol):
    name: str

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        ...


class MockLLMProvider:
    name = "mock"

    def __init__(self, verified_queries_provider: Callable[..., list[dict]] = list_verified_queries):
        self._verified_queries_provider = verified_queries_provider

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        question = request.question.strip()
        verified_query = _match_verified_query(
            question,
            self._verified_queries_provider(datasource_name=request.datasource_name),
        )
        if verified_query is not None:
            return SQLGenerationResult(
                sql=verified_query["sql"],
                provider=self.name,
                matched_query_id=verified_query["id"],
            )

        return SQLGenerationResult(
            sql=(
                "SELECT order_id, payment_amount FROM fact_orders "
                f"ORDER BY order_id LIMIT {request.default_browse_limit}"
            ),
            provider=self.name,
        )


def _match_verified_query(question: str, verified_queries: list[dict]) -> dict | None:
    normalized_question = _normalize_text(question)
    for query in verified_queries:
        verified_question = _normalize_text(query["question"])
        if normalized_question == verified_question:
            return query
    return None


def _normalize_text(text: str) -> str:
    return "".join(text.lower().split())


CHANGE_KINDS = {"dimension", "filter", "metric", "time", "none"}


def parse_sql_generation_content(
    content: str,
    *,
    expect_structured: bool,
) -> tuple[str, bool, str]:
    stripped = strip_code_fence(content)
    if not expect_structured:
        return stripped, False, "none"

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        raise ValueError("SQL generation response must be a JSON object.") from None

    if not isinstance(payload, dict):
        raise ValueError("SQL generation response must be a JSON object.")
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL generation response JSON must include sql.")
    return (
        strip_code_fence(sql),
        bool(payload.get("is_follow_up", False)),
        _valid_change_kind(payload.get("change_kind", "none")),
    )


def _valid_change_kind(change_kind: object) -> str:
    return change_kind if isinstance(change_kind, str) and change_kind in CHANGE_KINDS else "none"


def strip_code_fence(content: str) -> str:
    match = re.fullmatch(r"```(?:sql|json)?\s*(.*?)\s*```", content.strip(), flags=re.IGNORECASE | re.DOTALL)
    if match is not None:
        return match.group(1).strip()
    return content.strip()
