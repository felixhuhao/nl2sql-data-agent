from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.app.dataspace.verified_queries import list_verified_queries


@dataclass(frozen=True)
class SQLGenerationRequest:
    question: str
    schema_context: str


@dataclass(frozen=True)
class SQLGenerationResult:
    sql: str
    provider: str
    matched_query_id: str | None = None


class LLMProvider(Protocol):
    name: str

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        ...


class MockLLMProvider:
    name = "mock"

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        question = request.question.strip()
        unsafe_sql = _unsafe_sql_for_question(question)
        if unsafe_sql is not None:
            return SQLGenerationResult(sql=unsafe_sql, provider=self.name)

        verified_query = _match_verified_query(question)
        if verified_query is not None:
            return SQLGenerationResult(
                sql=verified_query.sql,
                provider=self.name,
                matched_query_id=verified_query.id,
            )

        return SQLGenerationResult(
            sql="SELECT order_id, payment_amount FROM fact_orders ORDER BY order_id LIMIT 20",
            provider=self.name,
        )


def _match_verified_query(question: str):
    normalized_question = _normalize_text(question)
    for query in list_verified_queries():
        verified_question = _normalize_text(query.question)
        if normalized_question == verified_question:
            return query
        if "最近30天" in normalized_question and {"销售额", "订单数"}.issubset(set(_question_terms(normalized_question))):
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
    return tuple(term for term in ("最近30天", "销售额", "订单数") if term in text)
