from __future__ import annotations

import json
import re
from typing import Any

import sqlglot
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlglot import exp

from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.metadata.models import (
    MetaAnalysisSpace,
    MetaColumn,
    MetaColumnAlias,
    MetaMetric,
    MetaTable,
    MetaVerifiedQuery,
    create_metadata_schema,
)


DEFAULT_TABLE_LIMIT = 5
DEFAULT_COLUMN_LIMIT = 20
DEFAULT_METRIC_LIMIT = 5
DEFAULT_VERIFIED_QUERY_LIMIT = 3
QUALIFIED_COLUMN_RE = re.compile(r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b")


def retrieve_metadata_assets(
    question: str,
    table_limit: int = DEFAULT_TABLE_LIMIT,
    column_limit: int = DEFAULT_COLUMN_LIMIT,
    metric_limit: int = DEFAULT_METRIC_LIMIT,
    verified_query_limit: int = DEFAULT_VERIFIED_QUERY_LIMIT,
) -> dict:
    _ensure_schema()
    normalized_question = _normalize_text(question)
    with sqlite_session() as session:
        analysis_space = _active_analysis_space(session)
        allowed_tables = _parse_json_set(analysis_space.tables if analysis_space else None)
        enabled_metrics = _parse_json_set(analysis_space.enabled_metrics if analysis_space else None)

        table_matches: dict[str, dict] = {}
        column_matches: dict[tuple[str, str], dict] = {}
        metric_matches: dict[str, dict] = {}
        verified_query_matches: dict[str, dict] = {}

        tables = _tables(session, allowed_tables)
        columns = _columns(session, allowed_tables)
        aliases_by_column = _aliases_by_column(session, allowed_tables)
        metrics = _metrics(session, enabled_metrics)
        verified_queries = _verified_queries(session)

        for table in tables:
            _match_table(normalized_question, table, table_matches)

        for column in columns:
            _match_column(
                normalized_question,
                column,
                aliases_by_column.get((column.table.table_name, column.column_name), []),
                table_matches,
                column_matches,
            )

        for metric in metrics:
            _match_metric(normalized_question, metric, table_matches, column_matches, metric_matches)

        for query in verified_queries:
            _match_verified_query(
                normalized_question,
                query,
                allowed_tables,
                table_matches,
                column_matches,
                verified_query_matches,
            )

        fallback_used = not any(
            (
                table_matches,
                column_matches,
                metric_matches,
                verified_query_matches,
            )
        )
        if fallback_used:
            for table in tables:
                _add_table_match(table_matches, table, 1, "fallback", source="fallback")

        return {
            "question": question,
            "normalized_question": normalized_question,
            "fallback_used": fallback_used,
            "tables": _rank(table_matches.values(), table_limit),
            "columns": _rank(column_matches.values(), column_limit),
            "metrics": _rank(metric_matches.values(), metric_limit),
            "verified_queries": _rank(verified_query_matches.values(), verified_query_limit),
        }


def _match_table(normalized_question: str, table: MetaTable, table_matches: dict[str, dict]) -> None:
    for value, score, reason in (
        (table.table_name, 10, "table_name"),
        (table.display_name, 8, "table_display_name"),
        (table.domain, 5, "table_domain"),
        (table.description, 4, "table_description"),
    ):
        if _contains(normalized_question, value):
            _add_table_match(table_matches, table, score, reason, source="direct_match")


def _match_column(
    normalized_question: str,
    column: MetaColumn,
    aliases: list[str],
    table_matches: dict[str, dict],
    column_matches: dict[tuple[str, str], dict],
) -> None:
    table_name = column.table.table_name
    for value, score, reason in (
        (column.column_name, 8, "column_name"),
        (column.description, 6, "column_description"),
    ):
        if _contains(normalized_question, value):
            _add_column_match(column_matches, column, score, reason)
            _add_table_match(
                table_matches,
                column.table,
                max(score - 2, 1),
                f"matched_column:{column.column_name}",
                source="direct_match",
            )

    matched_aliases = []
    for alias in aliases:
        if _contains(normalized_question, alias):
            matched_aliases.append(alias)
            _add_column_match(column_matches, column, 12, f"alias:{alias}", matched_alias=alias)
            _add_table_match(
                table_matches,
                column.table,
                10,
                f"matched_alias:{table_name}.{column.column_name}",
                source="direct_match",
            )

    for sample_value in _parse_json_list(column.sample_values):
        if _contains(normalized_question, str(sample_value)):
            _add_column_match(column_matches, column, 7, f"sample_value:{sample_value}")
            _add_table_match(
                table_matches,
                column.table,
                5,
                f"matched_sample:{table_name}.{column.column_name}",
                source="direct_match",
            )


def _match_metric(
    normalized_question: str,
    metric: MetaMetric,
    table_matches: dict[str, dict],
    column_matches: dict[tuple[str, str], dict],
    metric_matches: dict[str, dict],
) -> None:
    matched = False
    for value, score, reason in (
        (metric.name, 10, "metric_name"),
        (metric.label, 14, "metric_label"),
        (metric.description, 8, "metric_description"),
    ):
        if _contains(normalized_question, value):
            _add_metric_match(metric_matches, metric, score, reason)
            matched = True

    if not matched:
        return

    for table_name, column_name in _qualified_columns(metric.expression):
        _add_synthetic_table_match(
            table_matches,
            table_name,
            8,
            f"metric_expression:{metric.name}",
            source="metric_expansion",
        )
        _add_synthetic_column_match(column_matches, table_name, column_name, 7, f"metric_expression:{metric.name}")
    if metric.default_time_column:
        table_name, column_name = _split_qualified_name(metric.default_time_column)
        if table_name and column_name:
            _add_synthetic_table_match(
                table_matches,
                table_name,
                6,
                f"metric_time_column:{metric.name}",
                source="metric_expansion",
            )
            _add_synthetic_column_match(column_matches, table_name, column_name, 6, f"metric_time_column:{metric.name}")


def _match_verified_query(
    normalized_question: str,
    query: MetaVerifiedQuery,
    allowed_tables: set[str],
    table_matches: dict[str, dict],
    column_matches: dict[tuple[str, str], dict],
    verified_query_matches: dict[str, dict],
) -> None:
    score = 0
    reasons = []
    if normalized_question == _normalize_text(query.question):
        score += 30
        reasons.append("verified_question_exact")
    elif _contains(normalized_question, query.question) or _contains(_normalize_text(query.question), normalized_question):
        score += 16
        reasons.append("verified_question_partial")

    for tag in _parse_json_list(query.tags):
        if _contains(normalized_question, str(tag)):
            score += 4
            reasons.append(f"verified_tag:{tag}")

    if score == 0:
        return

    _add_verified_query_match(verified_query_matches, query, score, reasons)
    for table_name in _tables_from_sql(query.sql):
        if not allowed_tables or table_name in allowed_tables:
            _add_synthetic_table_match(
                table_matches,
                table_name,
                12,
                f"verified_query:{query.query_id}",
                source="verified_query",
            )
    for table_name, column_name in _qualified_columns(query.sql):
        if not allowed_tables or table_name in allowed_tables:
            _add_synthetic_column_match(column_matches, table_name, column_name, 10, f"verified_query:{query.query_id}")


def _add_table_match(
    matches: dict[str, dict],
    table: MetaTable,
    score: int,
    reason: str,
    source: str,
) -> None:
    _add_match(
        matches,
        table.table_name,
        {
            "table_name": table.table_name,
            "display_name": table.display_name,
            "description": table.description,
            "domain": table.domain,
            "row_count": table.row_count,
            "source": source,
        },
        score,
        reason,
        source=source,
    )


def _add_synthetic_table_match(
    matches: dict[str, dict],
    table_name: str,
    score: int,
    reason: str,
    source: str,
) -> None:
    _add_match(matches, table_name, {"table_name": table_name, "source": source}, score, reason, source=source)


def _add_column_match(
    matches: dict[tuple[str, str], dict],
    column: MetaColumn,
    score: int,
    reason: str,
    matched_alias: str | None = None,
) -> None:
    payload = {
        "table_name": column.table.table_name,
        "column_name": column.column_name,
        "data_type": column.data_type,
        "description": column.description,
        "is_dimension": column.is_dimension,
        "is_metric": column.is_metric,
        "sample_values": _parse_json_list(column.sample_values),
        "matched_aliases": [],
    }
    _add_match(matches, (column.table.table_name, column.column_name), payload, score, reason)
    if matched_alias:
        matches[(column.table.table_name, column.column_name)]["matched_aliases"].append(matched_alias)


def _add_synthetic_column_match(
    matches: dict[tuple[str, str], dict],
    table_name: str,
    column_name: str,
    score: int,
    reason: str,
) -> None:
    _add_match(
        matches,
        (table_name, column_name),
        {"table_name": table_name, "column_name": column_name, "matched_aliases": []},
        score,
        reason,
    )


def _add_metric_match(matches: dict[str, dict], metric: MetaMetric, score: int, reason: str) -> None:
    _add_match(
        matches,
        metric.name,
        {
            "name": metric.name,
            "label": metric.label,
            "expression": metric.expression,
            "description": metric.description,
            "default_time_column": metric.default_time_column,
            "allowed_dimensions": _parse_json_list(metric.allowed_dimensions),
        },
        score,
        reason,
    )


def _add_verified_query_match(
    matches: dict[str, dict],
    query: MetaVerifiedQuery,
    score: int,
    reasons: list[str],
) -> None:
    _add_match(
        matches,
        query.query_id,
        {
            "id": query.query_id,
            "question": query.question,
            "sql": query.sql,
            "tags": _parse_json_list(query.tags),
            "verified_by": query.verified_by,
        },
        score,
        reasons[0],
    )
    for reason in reasons[1:]:
        _add_match(matches, query.query_id, matches[query.query_id], 0, reason)


def _add_match(
    matches: dict[Any, dict],
    key: Any,
    payload: dict,
    score: int,
    reason: str,
    source: str | None = None,
) -> None:
    if key not in matches:
        matches[key] = {**payload, "score": 0, "reasons": []}
    matches[key]["score"] += score
    if reason not in matches[key]["reasons"]:
        matches[key]["reasons"].append(reason)
    if source and matches[key].get("source") != "direct_match":
        matches[key]["source"] = source


def _tables(session: Session, allowed_tables: set[str]) -> list[MetaTable]:
    if not allowed_tables:
        return []
    return session.scalars(
        select(MetaTable)
        .where(MetaTable.enabled.is_(True), MetaTable.table_name.in_(allowed_tables))
        .order_by(MetaTable.table_name)
    ).all()


def _columns(session: Session, allowed_tables: set[str]) -> list[MetaColumn]:
    if not allowed_tables:
        return []
    return session.scalars(
        select(MetaColumn)
        .join(MetaTable)
        .where(MetaTable.enabled.is_(True), MetaTable.table_name.in_(allowed_tables))
        .order_by(MetaTable.table_name, MetaColumn.id)
    ).all()


def _aliases_by_column(session: Session, allowed_tables: set[str]) -> dict[tuple[str, str], list[str]]:
    aliases: dict[tuple[str, str], list[str]] = {}
    if not allowed_tables:
        return aliases
    rows = session.scalars(
        select(MetaColumnAlias)
        .where(MetaColumnAlias.table_name.in_(allowed_tables))
        .order_by(MetaColumnAlias.table_name, MetaColumnAlias.column_name, MetaColumnAlias.alias)
    ).all()
    for row in rows:
        aliases.setdefault((row.table_name, row.column_name), []).append(row.alias)
    return aliases


def _metrics(session: Session, enabled_metrics: set[str]) -> list[MetaMetric]:
    if not enabled_metrics:
        return []
    return session.scalars(
        select(MetaMetric)
        .where(MetaMetric.enabled.is_(True), MetaMetric.name.in_(enabled_metrics))
        .order_by(MetaMetric.id)
    ).all()


def _verified_queries(session: Session) -> list[MetaVerifiedQuery]:
    return session.scalars(
        select(MetaVerifiedQuery)
        .where(MetaVerifiedQuery.enabled.is_(True))
        .order_by(MetaVerifiedQuery.id)
    ).all()


def _active_analysis_space(session: Session) -> MetaAnalysisSpace | None:
    return session.scalar(
        select(MetaAnalysisSpace)
        .where(MetaAnalysisSpace.enabled.is_(True))
        .order_by(MetaAnalysisSpace.id)
    )


def _tables_from_sql(sql: str) -> set[str]:
    try:
        expression = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.SqlglotError:
        return set()
    return {table.name for table in expression.find_all(exp.Table)}


def _qualified_columns(text: str) -> set[tuple[str, str]]:
    return set(QUALIFIED_COLUMN_RE.findall(text))


def _split_qualified_name(value: str) -> tuple[str | None, str | None]:
    parts = value.split(".", 1)
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


def _rank(matches, limit: int) -> list[dict]:
    return sorted(matches, key=lambda item: (-item["score"], _match_sort_key(item)))[:limit]


def _match_sort_key(item: dict) -> str:
    return item.get("table_name") or item.get("column_name") or item.get("name") or item.get("id") or ""


def _contains(normalized_text: str, candidate: str | None) -> bool:
    normalized_candidate = _normalize_text(candidate or "")
    return bool(normalized_text and normalized_candidate and normalized_candidate in normalized_text)


def _normalize_text(text: str) -> str:
    return "".join(str(text).lower().split())


def _parse_json_set(value: str | None) -> set[str]:
    return {str(item) for item in _parse_json_list(value)}


def _parse_json_list(value: str | None) -> list:
    if value is None:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _ensure_schema() -> None:
    create_metadata_schema(get_sqlite_engine())
