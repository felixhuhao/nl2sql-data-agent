from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.connectors.registry import get_datasource_dialect
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.vector.searcher import (
    ValueHit,
    VectorRetrievalResult,
    retrieve_vector_assets,
    search_values,
)
from backend.app.metadata.vector.store import VectorSearchHit
from backend.app.metadata.score_constants import MAX_LEXICAL_SCORE


RULE_WEIGHT = 0.3
VECTOR_WEIGHT = 0.6
PRIORITY_WEIGHT = 0.1
MAX_RULE_SCORE = MAX_LEXICAL_SCORE
QUALIFIED_COLUMN_RE = re.compile(r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b")


def hybrid_merge(
    rule_result: dict,
    question: str,
    *,
    table_limit: int,
    column_limit: int,
    metric_limit: int,
    verified_query_limit: int,
    datasource_name: str = DEFAULT_DATASOURCE,
    allowed_tables: set[str] | None = None,
    allowed_columns: set[tuple[str, str]] | None = None,
    datasource_dialect: str | None = None,
) -> dict:
    datasource_dialect = datasource_dialect or get_datasource_dialect(datasource_name)
    vector_result = retrieve_vector_assets(question, datasource_name=datasource_name)
    merged = {
        **rule_result,
        "retrieval_meta": _base_retrieval_meta(rule_result, vector_result),
    }

    table_matches = _items_by_key(rule_result.get("tables", []), "table_name")
    column_matches = _items_by_column_key(rule_result.get("columns", []))
    metric_matches = _items_by_key(rule_result.get("metrics", []), "name")
    verified_query_matches = _items_by_key(rule_result.get("verified_queries", []), "id")

    _merge_vector_hits(table_matches, vector_result.hits.get("tables", []), "table", _table_payload, merged)
    _merge_vector_hits(column_matches, vector_result.hits.get("columns", []), "column", _column_payload, merged)
    _merge_vector_hits(metric_matches, vector_result.hits.get("metrics", []), "metric", _metric_payload, merged)
    _merge_vector_hits(
        verified_query_matches,
        vector_result.hits.get("verified_queries", []),
        "verified_query",
        _verified_query_payload,
        merged,
    )
    if vector_result.vector_used and vector_result.index_status == "ready":
        _merge_value_hits(
            table_matches,
            column_matches,
            _safe_search_values(
                question,
                query_vector=vector_result.query_vector,
                datasource_name=datasource_name,
            ),
            merged,
        )

    _expand_metric_dependencies(
        table_matches,
        column_matches,
        metric_matches,
        merged,
        allowed_tables=allowed_tables or set(),
        allowed_columns=allowed_columns or set(),
    )
    _expand_verified_query_dependencies(
        table_matches,
        column_matches,
        verified_query_matches,
        merged,
        allowed_tables=allowed_tables or set(),
        allowed_columns=allowed_columns or set(),
        datasource_dialect=datasource_dialect,
    )

    merged["coverage_match_strength"] = _coverage_match_strength(
        (
            *table_matches.values(),
            *column_matches.values(),
            *metric_matches.values(),
            *verified_query_matches.values(),
        ),
        rule_floor=float(rule_result.get("coverage_match_strength", 0.0) or 0.0),
    )
    merged["tables"] = _rank_with_hybrid_score(table_matches.values(), table_limit, "table")
    merged["columns"] = _rank_with_hybrid_score(column_matches.values(), column_limit, "column")
    merged["metrics"] = _rank_with_hybrid_score(metric_matches.values(), metric_limit, "metric")
    merged["verified_queries"] = _rank_with_hybrid_score(
        verified_query_matches.values(),
        verified_query_limit,
        "verified_query",
    )
    merged["fallback_used"] = False
    return merged


def _base_retrieval_meta(rule_result: dict, vector_result: VectorRetrievalResult) -> dict:
    sources: dict[str, list[str]] = {}
    for table in rule_result.get("tables", []):
        _add_sources(sources, f"table:{table['table_name']}", _rule_sources(table))
    for column in rule_result.get("columns", []):
        key = f"column:{column['table_name']}.{column['column_name']}"
        _add_sources(sources, key, _rule_sources(column))
    for metric in rule_result.get("metrics", []):
        _add_sources(sources, f"metric:{metric['name']}", _rule_sources(metric))
    for query in rule_result.get("verified_queries", []):
        _add_sources(sources, f"verified_query:{query['id']}", _rule_sources(query))
    return {
        "vector_used": vector_result.vector_used,
        "index_status": vector_result.index_status,
        "stale_reason": vector_result.stale_reason,
        "sources": sources,
        "value_hits": [],
    }


def _merge_vector_hits(
    matches: dict[Any, dict],
    hits: list[VectorSearchHit],
    asset_type: str,
    payload_builder,
    result: dict,
) -> None:
    for hit in hits:
        payload = payload_builder(hit)
        key = _match_key(asset_type, payload)
        if key not in matches:
            matches[key] = {
                **payload,
                "score": 0,
                "reasons": [],
                "source": "vector",
            }
        matches[key]["_vector_score"] = max(matches[key].get("_vector_score", 0.0), hit.score)
        reason = f"vector:{hit.score:.2f}"
        if reason not in matches[key]["reasons"]:
            matches[key]["reasons"].append(reason)
        _add_sources(result["retrieval_meta"]["sources"], _asset_key(asset_type, payload), [reason])


def _merge_value_hits(
    table_matches: dict[Any, dict],
    column_matches: dict[Any, dict],
    value_hits: list[ValueHit],
    result: dict,
) -> None:
    for hit in value_hits:
        table_payload = {
            "table_name": hit.table_name,
            "source": "value_recall",
        }
        table_key = hit.table_name
        if table_key not in table_matches:
            table_matches[table_key] = {**table_payload, "score": 0, "reasons": []}
        table_matches[table_key]["_vector_score"] = max(table_matches[table_key].get("_vector_score", 0.0), hit.score)

        column_key = (hit.table_name, hit.column_name)
        if column_key not in column_matches:
            column_matches[column_key] = {
                "table_name": hit.table_name,
                "column_name": hit.column_name,
                "matched_aliases": [],
                "score": 0,
                "reasons": [],
            }
        column_matches[column_key]["_vector_score"] = max(
            column_matches[column_key].get("_vector_score", 0.0),
            hit.score,
        )

        reason = f"value:{hit.matched_value}"
        for item in (table_matches[table_key], column_matches[column_key]):
            if reason not in item["reasons"]:
                item["reasons"].append(reason)

        result["retrieval_meta"]["value_hits"].append(
            {
                "table_name": hit.table_name,
                "column_name": hit.column_name,
                "matched_value": hit.matched_value,
                "source": hit.source,
                "score": hit.score,
            }
        )
        _add_sources(result["retrieval_meta"]["sources"], f"table:{hit.table_name}", [reason])
        _add_sources(result["retrieval_meta"]["sources"], f"column:{hit.column_asset_id}", [reason])


def _expand_metric_dependencies(
    table_matches: dict[Any, dict],
    column_matches: dict[Any, dict],
    metric_matches: dict[Any, dict],
    result: dict,
    *,
    allowed_tables: set[str],
    allowed_columns: set[tuple[str, str]],
) -> None:
    for metric in metric_matches.values():
        metric_name = metric.get("name")
        if not metric_name:
            continue
        for table_name, column_name in _qualified_columns(str(metric.get("expression") or "")):
            if not _dependency_allowed(table_name, column_name, allowed_tables, allowed_columns):
                continue
            _add_dependency_table(
                table_matches,
                table_name,
                reason=f"metric_expression:{metric_name}",
                source="metric_expansion",
                result=result,
                score=7,
            )
            _add_dependency_column(
                column_matches,
                table_name,
                column_name,
                reason=f"metric_expression:{metric_name}",
                result=result,
                score=7,
            )
        default_time_column = metric.get("default_time_column")
        if default_time_column:
            table_name, column_name = _split_qualified_name(str(default_time_column))
            if table_name and column_name and _dependency_allowed(table_name, column_name, allowed_tables, allowed_columns):
                _add_sources(
                    result["retrieval_meta"]["sources"],
                    f"column:{table_name}.{column_name}",
                    [f"metric_default_time_column:{metric_name}"],
                )


def _expand_verified_query_dependencies(
    table_matches: dict[Any, dict],
    column_matches: dict[Any, dict],
    verified_query_matches: dict[Any, dict],
    result: dict,
    *,
    allowed_tables: set[str],
    allowed_columns: set[tuple[str, str]],
    datasource_dialect: str,
) -> None:
    for query in verified_query_matches.values():
        query_id = query.get("id")
        sql = str(query.get("sql") or "")
        if not query_id or not sql:
            continue
        for table_name in _tables_from_sql(sql, dialect=datasource_dialect):
            if allowed_tables and table_name not in allowed_tables:
                continue
            _add_dependency_table(
                table_matches,
                table_name,
                reason=f"verified_query:{query_id}",
                source="verified_query",
                result=result,
                score=10,
            )
        for table_name, column_name in _qualified_columns(sql):
            if not _dependency_allowed(table_name, column_name, allowed_tables, allowed_columns):
                continue
            _add_dependency_column(
                column_matches,
                table_name,
                column_name,
                reason=f"verified_query:{query_id}",
                result=result,
                score=10,
            )


def _add_dependency_table(
    table_matches: dict[Any, dict],
    table_name: str,
    *,
    reason: str,
    source: str,
    result: dict,
    score: float,
) -> None:
    if table_name not in table_matches:
        table_matches[table_name] = {
            "table_name": table_name,
            "source": source,
            "score": 0,
            "reasons": [],
        }
    table_matches[table_name]["score"] += score
    if reason not in table_matches[table_name]["reasons"]:
        table_matches[table_name]["reasons"].append(reason)
    _add_sources(result["retrieval_meta"]["sources"], f"table:{table_name}", [f"dependency:{reason}"])


def _add_dependency_column(
    column_matches: dict[Any, dict],
    table_name: str,
    column_name: str,
    *,
    reason: str,
    result: dict,
    score: float,
) -> None:
    key = (table_name, column_name)
    if key not in column_matches:
        column_matches[key] = {
            "table_name": table_name,
            "column_name": column_name,
            "matched_aliases": [],
            "score": 0,
            "reasons": [],
        }
    column_matches[key]["score"] += score
    if reason not in column_matches[key]["reasons"]:
        column_matches[key]["reasons"].append(reason)
    _add_sources(result["retrieval_meta"]["sources"], f"column:{table_name}.{column_name}", [f"dependency:{reason}"])


def _dependency_allowed(
    table_name: str,
    column_name: str,
    allowed_tables: set[str],
    allowed_columns: set[tuple[str, str]],
) -> bool:
    if allowed_tables and table_name not in allowed_tables:
        return False
    return not (allowed_columns and (table_name, column_name) not in allowed_columns)


def _safe_search_values(
    question: str,
    *,
    query_vector: list[float] | None = None,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> list[ValueHit]:
    try:
        if query_vector is None:
            return search_values(question, datasource_name=datasource_name)
        return search_values(question, query_vector=query_vector, datasource_name=datasource_name)
    except Exception:
        return []


def _rank_with_hybrid_score(items, limit: int, asset_type: str) -> list[dict]:
    ranked = []
    for item in items:
        item = dict(item)
        rule_score = float(item.get("score", 0))
        item.pop("_rule_score", None)
        vector_score = float(item.pop("_vector_score", 0.0))
        priority = _business_priority(asset_type)
        item["score"] = (
            RULE_WEIGHT * min(rule_score / MAX_RULE_SCORE, 1.0)
            + VECTOR_WEIGHT * vector_score
            + PRIORITY_WEIGHT * priority
        )
        ranked.append(item)
    return sorted(ranked, key=lambda item: (-item["score"], _sort_key(item)))[:limit]


def _coverage_match_strength(items, *, rule_floor: float = 0.0) -> float:
    strengths = [_clamp01(rule_floor)]
    for item in items:
        rule_score = float(item.get("score", 0.0) or 0.0)
        vector_score = float(item.get("_vector_score", 0.0) or 0.0)
        strengths.append(_clamp01(rule_score / MAX_RULE_SCORE))
        strengths.append(_clamp01(vector_score))
    return max(strengths, default=0.0)


def _clamp01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _table_payload(hit: VectorSearchHit) -> dict:
    metadata = hit.metadata
    return {
        "table_name": str(metadata.get("table_name") or hit.asset_id),
        "display_name": metadata.get("display_name"),
        "description": metadata.get("description"),
        "domain": metadata.get("domain"),
        "row_count": metadata.get("row_count", 0),
        "engine": metadata.get("engine"),
        "partition_key": metadata.get("partition_key"),
        "sorting_key": metadata.get("sorting_key"),
        "source": "vector",
    }


def _column_payload(hit: VectorSearchHit) -> dict:
    metadata = hit.metadata
    table_name, column_name = _split_column_asset_id(hit.asset_id)
    return {
        "table_name": str(metadata.get("table_name") or table_name),
        "column_name": str(metadata.get("column_name") or column_name),
        "data_type": metadata.get("data_type"),
        "description": metadata.get("description"),
        "nullable": bool(metadata.get("nullable", False)),
        "is_dimension": bool(metadata.get("is_dimension", False)),
        "is_metric": bool(metadata.get("is_metric", False)),
        "sample_values": metadata.get("sample_values", []),
        "is_partition_key": bool(metadata.get("is_partition_key", False)),
        "is_sorting_key": bool(metadata.get("is_sorting_key", False)),
        "is_primary_key": bool(metadata.get("is_primary_key", False)),
        "low_cardinality": bool(metadata.get("low_cardinality", False)),
        "matched_aliases": [],
    }


def _metric_payload(hit: VectorSearchHit) -> dict:
    metadata = hit.metadata
    return {
        "name": str(metadata.get("name") or hit.asset_id),
        "label": metadata.get("label"),
        "expression": metadata.get("expression"),
        "description": metadata.get("description"),
        "default_time_column": metadata.get("default_time_column"),
        "allowed_dimensions": metadata.get("allowed_dimensions", []),
    }


def _verified_query_payload(hit: VectorSearchHit) -> dict:
    metadata = hit.metadata
    return {
        "id": str(metadata.get("query_id") or hit.asset_id),
        "question": metadata.get("question"),
        "sql": metadata.get("sql"),
        "tags": metadata.get("tags", []),
        "verified_by": metadata.get("verified_by"),
    }


def _items_by_key(items: list[dict], key: str) -> dict[Any, dict]:
    result = {}
    for item in items:
        copied = dict(item)
        result[copied[key]] = copied
    return result


def _items_by_column_key(items: list[dict]) -> dict[tuple[str, str], dict]:
    result = {}
    for item in items:
        copied = dict(item)
        result[(copied["table_name"], copied["column_name"])] = copied
    return result


def _rule_sources(item: dict) -> list[str]:
    return [f"rule:{reason}" for reason in item.get("reasons", [])]


def _add_sources(sources: dict[str, list[str]], key: str, values: list[str]) -> None:
    if not values:
        return
    sources.setdefault(key, [])
    for value in values:
        if value not in sources[key]:
            sources[key].append(value)


def _match_key(asset_type: str, payload: dict):
    if asset_type == "column":
        return (payload["table_name"], payload["column_name"])
    if asset_type == "table":
        return payload["table_name"]
    if asset_type == "metric":
        return payload["name"]
    return payload["id"]


def _asset_key(asset_type: str, payload: dict) -> str:
    if asset_type == "column":
        return f"column:{payload['table_name']}.{payload['column_name']}"
    if asset_type == "table":
        return f"table:{payload['table_name']}"
    if asset_type == "metric":
        return f"metric:{payload['name']}"
    return f"verified_query:{payload['id']}"


def _split_column_asset_id(asset_id: str) -> tuple[str, str]:
    stripped_asset_id = _strip_datasource_prefix(asset_id)
    table_name, _, column_name = stripped_asset_id.partition(".")
    return table_name, column_name


def _split_qualified_name(value: str) -> tuple[str | None, str | None]:
    table_name, separator, column_name = value.partition(".")
    if not separator:
        return None, None
    return table_name, column_name


def _strip_datasource_prefix(asset_id: str) -> str:
    first, separator, rest = asset_id.partition(":")
    if separator and "." not in first:
        return rest
    return asset_id


def _qualified_columns(text: str) -> set[tuple[str, str]]:
    return set(QUALIFIED_COLUMN_RE.findall(text))


def _tables_from_sql(sql: str, *, dialect: str = "duckdb") -> set[str]:
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return set()
    return {table.name for table in expression.find_all(exp.Table)}


def _business_priority(asset_type: str) -> float:
    if asset_type == "verified_query":
        return 1.0
    if asset_type == "metric":
        return 0.8
    if asset_type == "table":
        return 0.3
    return 0.0


def _sort_key(item: dict) -> str:
    return item.get("table_name") or item.get("column_name") or item.get("name") or item.get("id") or ""
