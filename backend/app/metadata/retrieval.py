from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.config import get_settings, vector_config_allows_attempt
from backend.app.connectors.registry import get_datasource_dialect
from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.metadata.hybrid import hybrid_merge
from backend.app.metadata.models import (
    DEFAULT_DATASOURCE,
    MetaAnalysisSpace,
    MetaColumn,
    MetaColumnAlias,
    MetaMetric,
    MetaTable,
    MetaVerifiedQuery,
    create_metadata_schema,
)
from backend.app.metadata.vector.searcher import is_recallable_value


DEFAULT_TABLE_LIMIT = 5
DEFAULT_COLUMN_LIMIT = 20
DEFAULT_METRIC_LIMIT = 5
DEFAULT_VERIFIED_QUERY_LIMIT = 3
QUALIFIED_COLUMN_RE = re.compile(r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b")
ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
CJK_SEGMENT_RE = re.compile(r"[\u3400-\u9fff]+")
TIME_INTENT_PATTERNS = (
    re.compile(r"(最近|近|过去)\d+(天|日|周|月|年)"),
    re.compile(r"\d{4}年\d{1,2}月"),
    re.compile(r"\d{4}[-/]\d{1,2}"),
    re.compile(
        r"(最近|过去|今日|今天|昨日|昨天|本周|上周|每周|按周|周度|本月|上月|每月|按月|"
        r"月度|月份|本季|上季|季度|今年|去年|每年|按年|年度|每日|按日|按天|"
        r"日期|时间|趋势|同比|环比)"
    ),
)

# Lexical scores are a deterministic fallback when vectors are disabled or stale.
# Candidate coverage dominates so short aliases/labels require most of their terms
# to be present; query signal breaks ties without rewarding one noisy unigram.
LEXICAL_MIN_SCORE = 3.0
VERIFIED_QUERY_MIN_SCORE = 8.0
LEXICAL_CANDIDATE_COVERAGE_WEIGHT = 0.85
LEXICAL_QUERY_SIGNAL_WEIGHT = 0.15
LEXICAL_QUERY_SIGNAL_SCALE = 4.0
LEXICAL_EXACT_MATCH_BONUS = 0.5
SALES_SHARE_CONCEPT_TEXT = "占比 比例 比重 贡献 share ratio proportion percent percentage pct"
SALES_METRIC_CONCEPT_TEXT = "销售 销售额 营收 收入 成交额 sales sale revenue gmv amount"
PRIMARY_MATCH_SOURCES = {"direct_match", "lexical"}


@dataclass(frozen=True)
class QueryProfile:
    raw_text: str
    normalized_text: str
    terms: frozenset[str]


@dataclass(frozen=True)
class WeightedText:
    value: str | None
    weight: float
    reason: str


@dataclass(frozen=True)
class FieldScore:
    score: float
    reason: str
    value: str


def retrieve_metadata_assets(
    question: str,
    table_limit: int = DEFAULT_TABLE_LIMIT,
    column_limit: int = DEFAULT_COLUMN_LIMIT,
    metric_limit: int = DEFAULT_METRIC_LIMIT,
    verified_query_limit: int = DEFAULT_VERIFIED_QUERY_LIMIT,
    use_vector: bool | None = None,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> dict:
    _ensure_schema()
    profile = _query_profile(question)
    with sqlite_session() as session:
        analysis_space = _active_analysis_space(session, datasource_name=datasource_name)
        allowed_tables = _parse_json_set(analysis_space.tables if analysis_space else None)
        enabled_metrics = _parse_json_set(analysis_space.enabled_metrics if analysis_space else None)
        datasource_dialect = get_datasource_dialect(datasource_name)

        table_matches: dict[str, dict] = {}
        column_matches: dict[tuple[str, str], dict] = {}
        metric_matches: dict[str, dict] = {}
        verified_query_matches: dict[str, dict] = {}

        tables = _tables(session, allowed_tables, datasource_name=datasource_name)
        columns = _columns(session, allowed_tables, datasource_name=datasource_name)
        aliases_by_column = _aliases_by_column(session, allowed_tables, datasource_name=datasource_name)
        metrics = _metrics(session, enabled_metrics, datasource_name=datasource_name)
        verified_queries = _verified_queries(session, datasource_name=datasource_name)
        allowed_columns = {(column.table.table_name, column.column_name) for column in columns}

        for table in tables:
            _match_table(profile, table, table_matches)

        for column in columns:
            _match_column(
                profile,
                column,
                aliases_by_column.get((column.table.table_name, column.column_name), []),
                table_matches,
                column_matches,
            )

        for metric in metrics:
            _match_metric(
                profile,
                metric,
                table_matches,
                column_matches,
                metric_matches,
                allowed_tables=allowed_tables,
                allowed_columns=allowed_columns,
            )

        for query in verified_queries:
            _match_verified_query(
                profile,
                query,
                allowed_tables,
                table_matches,
                column_matches,
                verified_query_matches,
                datasource_dialect=datasource_dialect,
                allowed_columns=allowed_columns,
            )

        result = {
            "question": question,
            "datasource": datasource_name,
            "normalized_question": profile.normalized_text,
            "fallback_used": False,
            "tables": _rank(table_matches.values(), table_limit),
            "columns": _rank(column_matches.values(), column_limit),
            "metrics": _rank(metric_matches.values(), metric_limit),
            "verified_queries": _rank(verified_query_matches.values(), verified_query_limit),
        }
        if _should_use_vector(use_vector):
            result = hybrid_merge(
                result,
                question,
                table_limit=table_limit,
                column_limit=column_limit,
                metric_limit=metric_limit,
                verified_query_limit=verified_query_limit,
                datasource_name=datasource_name,
                allowed_tables=allowed_tables,
                allowed_columns=allowed_columns,
                datasource_dialect=datasource_dialect,
            )

        fallback_used = not any(
            (
                result["tables"],
                result["columns"],
                result["metrics"],
                result["verified_queries"],
            )
        )
        if fallback_used:
            fallback_table_matches: dict[str, dict] = {}
            for table in tables:
                _add_table_match(fallback_table_matches, table, 1, "fallback", source="fallback")
            result["tables"] = _rank(fallback_table_matches.values(), table_limit)
            result["fallback_used"] = True
        return result


def _should_use_vector(use_vector: bool | None) -> bool:
    if use_vector is not None:
        return use_vector
    return vector_config_allows_attempt(get_settings())


def _match_table(profile: QueryProfile, table: MetaTable, table_matches: dict[str, dict]) -> None:
    for match in _score_fields(
        profile,
        (
            WeightedText(table.table_name, 10, "table_name"),
            WeightedText(table.display_name, 8, "table_display_name"),
            WeightedText(table.domain, 5, "table_domain"),
            WeightedText(table.description, 4, "table_description"),
        ),
    ):
        _add_table_match(table_matches, table, match.score, match.reason, source="lexical")


def _match_column(
    profile: QueryProfile,
    column: MetaColumn,
    aliases: list[str],
    table_matches: dict[str, dict],
    column_matches: dict[tuple[str, str], dict],
) -> None:
    table_name = column.table.table_name
    for match in _score_fields(
        profile,
        (
            WeightedText(column.column_name, 8, "column_name"),
            WeightedText(column.description, 6, "column_description"),
        ),
    ):
        _add_column_match(column_matches, column, match.score, match.reason)
        _add_table_match(
            table_matches,
            column.table,
            max(match.score - 2, 1),
            f"matched_column:{column.column_name}",
            source="lexical",
        )

    for alias in aliases:
        match = _score_field(profile, WeightedText(alias, 12, f"alias:{alias}"))
        if match is None:
            continue
        _add_column_match(column_matches, column, match.score, match.reason, matched_alias=alias)
        _add_table_match(
            table_matches,
            column.table,
            max(match.score - 2, 1),
            f"matched_alias:{table_name}.{column.column_name}",
            source="lexical",
        )

    for sample_value in _parse_json_list(column.sample_values):
        value_text = str(sample_value)
        if not is_recallable_value(value_text):
            continue
        match = _score_field(profile, WeightedText(value_text, 7, f"sample_value:{sample_value}"))
        if match is None:
            continue
        _add_column_match(column_matches, column, match.score, match.reason)
        _add_table_match(
            table_matches,
            column.table,
            max(match.score - 2, 1),
            f"matched_sample:{table_name}.{column.column_name}",
            source="lexical",
        )


def _match_metric(
    profile: QueryProfile,
    metric: MetaMetric,
    table_matches: dict[str, dict],
    column_matches: dict[tuple[str, str], dict],
    metric_matches: dict[str, dict],
    *,
    allowed_tables: set[str],
    allowed_columns: set[tuple[str, str]],
) -> None:
    matched = False
    for match in _score_fields(
        profile,
        (
            WeightedText(metric.name, 10, "metric_name"),
            WeightedText(metric.label, 14, "metric_label"),
            WeightedText(metric.description, 8, "metric_description"),
            WeightedText(metric.expression, 3, "metric_expression_text"),
        ),
    ):
        _add_metric_match(metric_matches, metric, match.score, match.reason)
        matched = True

    if not matched and _matches_sales_share_intent(profile, metric):
        _add_metric_match(metric_matches, metric, 12, "metric_sales_share_intent")
        matched = True

    if not matched:
        return

    for table_name, column_name in _qualified_columns(metric.expression):
        if allowed_tables and table_name not in allowed_tables:
            continue
        if allowed_columns and (table_name, column_name) not in allowed_columns:
            continue
        _add_synthetic_table_match(
            table_matches,
            table_name,
            8,
            f"metric_expression:{metric.name}",
            source="metric_expansion",
        )
        _add_synthetic_column_match(column_matches, table_name, column_name, 7, f"metric_expression:{metric.name}")
    if metric.default_time_column and _has_time_intent(profile):
        table_name, column_name = _split_qualified_name(metric.default_time_column)
        if table_name and column_name:
            if allowed_tables and table_name not in allowed_tables:
                return
            if allowed_columns and (table_name, column_name) not in allowed_columns:
                return
            _add_synthetic_table_match(
                table_matches,
                table_name,
                6,
                f"metric_time_column:{metric.name}",
                source="metric_expansion",
            )
            _add_synthetic_column_match(column_matches, table_name, column_name, 6, f"metric_time_column:{metric.name}")


def _match_verified_query(
    profile: QueryProfile,
    query: MetaVerifiedQuery,
    allowed_tables: set[str],
    table_matches: dict[str, dict],
    column_matches: dict[tuple[str, str], dict],
    verified_query_matches: dict[str, dict],
    *,
    datasource_dialect: str,
    allowed_columns: set[tuple[str, str]],
) -> None:
    score = 0
    reasons = []
    if profile.normalized_text == _normalize_text(query.question):
        score += 30
        reasons.append("verified_question_exact")
    else:
        match = _score_field(
            profile,
            WeightedText(query.question, 16, "verified_question_semantic"),
            min_score=VERIFIED_QUERY_MIN_SCORE,
        )
        if match is not None:
            score += match.score
            reasons.append(match.reason)

    for tag in _parse_json_list(query.tags):
        match = _score_field(profile, WeightedText(str(tag), 4, f"verified_tag:{tag}"))
        if match is not None:
            score += match.score
            reasons.append(match.reason)

    if score == 0:
        return

    _add_verified_query_match(verified_query_matches, query, score, reasons)
    for table_name in _tables_from_sql(query.sql, dialect=datasource_dialect):
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
            if allowed_columns and (table_name, column_name) not in allowed_columns:
                continue
            _add_synthetic_column_match(column_matches, table_name, column_name, 10, f"verified_query:{query.query_id}")


def _add_table_match(
    matches: dict[str, dict],
    table: MetaTable,
    score: float,
    reason: str,
    source: str,
) -> None:
    _add_match(
        matches,
        table.table_name,
        {
            "table_name": table.table_name,
            "datasource": table.datasource,
            "display_name": table.display_name,
            "description": table.description,
            "domain": table.domain,
            "row_count": table.row_count,
            "engine": table.engine,
            "partition_key": table.partition_key,
            "sorting_key": table.sorting_key,
            "source": source,
        },
        score,
        reason,
        source=source,
    )


def _add_synthetic_table_match(
    matches: dict[str, dict],
    table_name: str,
    score: float,
    reason: str,
    source: str,
) -> None:
    _add_match(matches, table_name, {"table_name": table_name, "source": source}, score, reason, source=source)


def _add_column_match(
    matches: dict[tuple[str, str], dict],
    column: MetaColumn,
    score: float,
    reason: str,
    matched_alias: str | None = None,
) -> None:
    payload = {
        "table_name": column.table.table_name,
        "datasource": column.datasource,
        "column_name": column.column_name,
        "data_type": column.data_type,
        "description": column.description,
        "nullable": column.nullable,
        "is_dimension": column.is_dimension,
        "is_metric": column.is_metric,
        "sample_values": _parse_json_list(column.sample_values),
        "is_partition_key": column.is_partition_key,
        "is_sorting_key": column.is_sorting_key,
        "is_primary_key": column.is_primary_key,
        "low_cardinality": column.low_cardinality,
        "matched_aliases": [],
    }
    _add_match(matches, (column.table.table_name, column.column_name), payload, score, reason)
    if matched_alias:
        matches[(column.table.table_name, column.column_name)]["matched_aliases"].append(matched_alias)


def _add_synthetic_column_match(
    matches: dict[tuple[str, str], dict],
    table_name: str,
    column_name: str,
    score: float,
    reason: str,
) -> None:
    _add_match(
        matches,
        (table_name, column_name),
        {"table_name": table_name, "column_name": column_name, "matched_aliases": []},
        score,
        reason,
    )


def _add_metric_match(matches: dict[str, dict], metric: MetaMetric, score: float, reason: str) -> None:
    _add_match(
        matches,
        metric.name,
        {
            "name": metric.name,
            "datasource": metric.datasource,
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
    score: float,
    reasons: list[str],
) -> None:
    _add_match(
        matches,
        query.query_id,
        {
            "id": query.query_id,
            "datasource": query.datasource,
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
    score: float,
    reason: str,
    source: str | None = None,
) -> None:
    if key not in matches:
        matches[key] = {**payload, "score": 0, "reasons": []}
    matches[key]["score"] += score
    if reason not in matches[key]["reasons"]:
        matches[key]["reasons"].append(reason)
    if source and matches[key].get("source") not in PRIMARY_MATCH_SOURCES:
        matches[key]["source"] = source


def _tables(
    session: Session,
    allowed_tables: set[str],
    datasource_name: str = DEFAULT_DATASOURCE,
) -> list[MetaTable]:
    if not allowed_tables:
        return []
    return list(session.scalars(
        select(MetaTable)
        .where(
            MetaTable.enabled.is_(True),
            MetaTable.datasource == datasource_name,
            MetaTable.table_name.in_(allowed_tables),
        )
        .order_by(MetaTable.table_name)
    ).all())


def _columns(
    session: Session,
    allowed_tables: set[str],
    datasource_name: str = DEFAULT_DATASOURCE,
) -> list[MetaColumn]:
    if not allowed_tables:
        return []
    return list(session.scalars(
        select(MetaColumn)
        .join(MetaTable)
        .where(
            MetaTable.enabled.is_(True),
            MetaTable.datasource == datasource_name,
            MetaTable.table_name.in_(allowed_tables),
        )
        .order_by(MetaTable.table_name, MetaColumn.id)
    ).all())


def _aliases_by_column(
    session: Session,
    allowed_tables: set[str],
    datasource_name: str = DEFAULT_DATASOURCE,
) -> dict[tuple[str, str], list[str]]:
    aliases: dict[tuple[str, str], list[str]] = {}
    if not allowed_tables:
        return aliases
    rows = session.scalars(
        select(MetaColumnAlias)
        .where(
            MetaColumnAlias.datasource == datasource_name,
            MetaColumnAlias.table_name.in_(allowed_tables),
        )
        .order_by(MetaColumnAlias.table_name, MetaColumnAlias.column_name, MetaColumnAlias.alias)
    ).all()
    for row in rows:
        aliases.setdefault((row.table_name, row.column_name), []).append(row.alias)
    return aliases


def _metrics(
    session: Session,
    enabled_metrics: set[str],
    datasource_name: str = DEFAULT_DATASOURCE,
) -> list[MetaMetric]:
    if not enabled_metrics:
        return []
    return list(session.scalars(
        select(MetaMetric)
        .where(
            MetaMetric.enabled.is_(True),
            MetaMetric.datasource == datasource_name,
            MetaMetric.name.in_(enabled_metrics),
        )
        .order_by(MetaMetric.id)
    ).all())


def _verified_queries(
    session: Session,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> list[MetaVerifiedQuery]:
    return list(session.scalars(
        select(MetaVerifiedQuery)
        .where(
            MetaVerifiedQuery.enabled.is_(True),
            MetaVerifiedQuery.datasource == datasource_name,
        )
        .order_by(MetaVerifiedQuery.id)
    ).all())


def _active_analysis_space(
    session: Session,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> MetaAnalysisSpace | None:
    analysis_space = session.scalar(
        select(MetaAnalysisSpace)
        .where(
            MetaAnalysisSpace.enabled.is_(True),
            MetaAnalysisSpace.datasource == datasource_name,
        )
        .order_by(MetaAnalysisSpace.id)
    )
    if analysis_space is None and datasource_name == DEFAULT_DATASOURCE:
        return session.scalar(
            select(MetaAnalysisSpace)
            .where(MetaAnalysisSpace.enabled.is_(True))
            .order_by(MetaAnalysisSpace.id)
        )
    return analysis_space


def _tables_from_sql(sql: str, *, dialect: str = "duckdb") -> set[str]:
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
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


def _query_profile(text: str) -> QueryProfile:
    return QueryProfile(
        raw_text=text,
        normalized_text=_normalize_text(text),
        terms=frozenset(_lexical_terms(text)),
    )


def _score_fields(
    profile: QueryProfile,
    fields: tuple[WeightedText, ...],
    *,
    min_score: float = LEXICAL_MIN_SCORE,
) -> list[FieldScore]:
    scored = []
    for field in fields:
        match = _score_field(profile, field, min_score=min_score)
        if match is not None:
            scored.append(match)
    return scored


def _score_field(
    profile: QueryProfile,
    field: WeightedText,
    *,
    min_score: float = LEXICAL_MIN_SCORE,
) -> FieldScore | None:
    if not field.value:
        return None
    score = _lexical_score(profile, field.value, field.weight)
    if score < min_score:
        return None
    return FieldScore(score=score, reason=field.reason, value=str(field.value))


def _lexical_score(profile: QueryProfile, candidate: str, weight: float) -> float:
    if not profile.terms:
        return 0.0
    candidate_terms = _lexical_terms(candidate)
    if not candidate_terms:
        return 0.0

    overlap = candidate_terms & profile.terms
    if not overlap:
        return 0.0

    coverage = len(overlap) / len(candidate_terms)
    query_signal = min(len(overlap) / max(len(profile.terms), 1) * LEXICAL_QUERY_SIGNAL_SCALE, 1.0)
    score = weight * (
        (LEXICAL_CANDIDATE_COVERAGE_WEIGHT * coverage)
        + (LEXICAL_QUERY_SIGNAL_WEIGHT * query_signal)
    )
    if _normalize_text(candidate) == profile.normalized_text:
        score += weight * LEXICAL_EXACT_MATCH_BONUS
    return score


def _lexical_terms(text: str, *, include_cjk_unigrams: bool = False) -> set[str]:
    normalized = str(text).casefold()
    terms: set[str] = set()

    for token in ASCII_TOKEN_RE.findall(normalized):
        terms.update(_ascii_term_variants(token))

    for segment in CJK_SEGMENT_RE.findall(normalized):
        terms.update(_cjk_terms(segment, include_unigrams=include_cjk_unigrams))

    return terms


def _ascii_term_variants(token: str) -> set[str]:
    if not token:
        return set()
    variants = {token}
    if len(token) > 3 and token.endswith("ies"):
        variants.add(f"{token[:-3]}y")
    if len(token) > 4 and token.endswith(("ches", "shes", "xes", "zes", "ses")):
        variants.add(token[:-2])
    if len(token) > 4 and token.endswith("ied"):
        variants.add(f"{token[:-3]}y")
    elif len(token) > 4 and token.endswith("ed"):
        variants.add(token[:-2])
        if token[:-2].endswith("at"):
            variants.add(f"{token[:-2]}e")
    if len(token) > 5 and token.endswith("ing"):
        variants.add(token[:-3])
        variants.add(f"{token[:-3]}e")
    if len(token) > 3 and token.endswith("s"):
        variants.add(token[:-1])
    return variants


def _cjk_terms(segment: str, *, include_unigrams: bool = False) -> set[str]:
    terms: set[str] = set()
    if not segment:
        return terms
    if include_unigrams or len(segment) == 1:
        terms.update(segment)
    for ngram_size in range(2, min(4, len(segment)) + 1):
        for index in range(0, len(segment) - ngram_size + 1):
            terms.add(segment[index : index + ngram_size])
    if len(segment) <= 8:
        terms.add(segment)
    return terms


def _has_time_intent(profile: QueryProfile) -> bool:
    return any(pattern.search(profile.normalized_text) for pattern in TIME_INTENT_PATTERNS)


def _matches_sales_share_intent(profile: QueryProfile, metric: MetaMetric) -> bool:
    return _has_concept(profile, SALES_SHARE_CONCEPT_TEXT) and _metric_matches_concept(
        metric,
        SALES_METRIC_CONCEPT_TEXT,
    )


def _has_concept(profile: QueryProfile, concept_text: str) -> bool:
    return bool(profile.terms & _lexical_terms(concept_text))


def _metric_matches_concept(metric: MetaMetric, concept_text: str) -> bool:
    concept_profile = _query_profile(concept_text)
    metric_text = _join_text(metric.name, metric.label)
    return _score_field(
        concept_profile,
        WeightedText(metric_text, 8, "metric_concept"),
        min_score=LEXICAL_MIN_SCORE,
    ) is not None


def _normalize_text(text: str) -> str:
    return "".join(str(text).lower().split())


def _join_text(*values: object | None) -> str:
    return " ".join(str(value).strip() for value in values if value is not None and str(value).strip())


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
