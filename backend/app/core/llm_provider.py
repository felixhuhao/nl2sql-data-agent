from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

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
        unsafe_sql = _unsafe_sql_for_question(question)
        if unsafe_sql is not None:
            return SQLGenerationResult(sql=unsafe_sql, provider=self.name)

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
            sql="SELECT order_id, payment_amount FROM fact_orders ORDER BY order_id LIMIT 20",
            provider=self.name,
        )


def _match_verified_query(question: str, verified_queries: list[dict]) -> dict | None:
    normalized_question = _normalize_text(question)
    for query in verified_queries:
        verified_question = _normalize_text(query["question"])
        if normalized_question == verified_question:
            return query
        query_tags = set(query["tags"])
        question_terms = set(_question_terms(normalized_question))
        if "最近30天" in normalized_question and {"sales", "time_series"}.issubset(query_tags) and {
            "销售额",
            "订单数",
        }.issubset(question_terms):
            return query
        if "最近30天" in normalized_question and {"sales", "region"}.issubset(query_tags) and {
            "地区",
            "销售额",
        }.issubset(question_terms):
            return query
        if "最近30天" in normalized_question and {"sales", "channel"}.issubset(query_tags) and {
            "渠道",
            "销售额",
        }.issubset(question_terms):
            return query
        if "最近30天" in normalized_question and {"product", "topn"}.issubset(query_tags) and {
            "商品",
            "销量",
        }.issubset(question_terms):
            return query
    return None


def _unsafe_sql_for_question(question: str) -> str | None:
    normalized_question = _normalize_text(question)
    if any(keyword in normalized_question for keyword in ("删除", "delete")):
        return "DELETE FROM fact_orders WHERE order_date >= DATE '2024-01-01'"
    if any(keyword in normalized_question for keyword in ("drop", "删表")):
        return "DROP TABLE fact_orders"
    if any(keyword in normalized_question for keyword in ("create", "建表", "创建")):
        return "CREATE TABLE tmp_orders AS SELECT * FROM fact_orders"
    return None


def _normalize_text(text: str) -> str:
    return "".join(text.lower().split())


def _question_terms(text: str) -> tuple[str, ...]:
    return tuple(term for term in ("最近30天", "销售额", "订单数", "地区", "渠道", "商品", "销量") if term in text)


CHANGE_KINDS = {"dimension", "filter", "metric", "time", "none"}
SQL_START_RE = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)


def parse_sql_generation_content(content: str, *, expect_structured: bool) -> tuple[str, bool, str]:
    stripped = strip_code_fence(content)
    if not expect_structured:
        return stripped, False, "none"

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        if SQL_START_RE.match(stripped):
            return stripped, False, "none"
        raise ValueError("SQL generation response did not include extractable SQL.") from None

    if not isinstance(payload, dict):
        raise ValueError("SQL generation response must be a JSON object.")
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL generation response JSON must include sql.")
    change_kind = payload.get("change_kind", "none")
    if change_kind not in CHANGE_KINDS:
        change_kind = "none"
    return strip_code_fence(sql), bool(payload.get("is_follow_up", False)), change_kind


def strip_code_fence(content: str) -> str:
    match = re.fullmatch(r"```(?:sql|json)?\s*(.*?)\s*```", content.strip(), flags=re.IGNORECASE | re.DOTALL)
    if match is not None:
        return match.group(1).strip()
    return content.strip()
