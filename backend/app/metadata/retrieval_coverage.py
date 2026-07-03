from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.metadata.models import (
    DEFAULT_DATASOURCE,
    MetaAnalysisSpace,
    MetaRelationship,
    create_metadata_schema,
)


MAX_LEXICAL_SCORE = 30.0


@dataclass
class RetrievalCoverage:
    match_strength: float
    structural_score: float
    score: float
    band: str
    expanded: bool = False
    fallback_used: bool = False
    signals: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_coverage(
    retrieval_result: dict,
    datasource_name: str = DEFAULT_DATASOURCE,
    *,
    olap_intents: list[str] | None = None,
) -> RetrievalCoverage:
    settings = get_settings()
    match_strength = _match_strength(retrieval_result)
    table_names = _retrieved_table_names(retrieval_result)
    metric_intent = bool(retrieval_result.get("metrics")) or bool(olap_intents)

    relationships = _relationships(datasource_name)
    allowed_tables = _allowed_tables(datasource_name)
    fact_roles = _fact_role_tables(
        relationships,
        allowed_tables,
        min_dim_edges=int(getattr(settings, "retrieval_fact_min_dim_edges", 2)),
    )
    structural_score, structural_signals = _structural_score(
        table_names,
        relationships,
        fact_roles,
        metric_intent=metric_intent,
    )

    if relationships:
        strength_weight = float(getattr(settings, "retrieval_coverage_strength_weight", 0.5))
        structural_weight = float(getattr(settings, "retrieval_coverage_structural_weight", 0.5))
        denominator = strength_weight + structural_weight
        if denominator <= 0:
            score = match_strength
        else:
            score = (
                strength_weight * match_strength
                + structural_weight * structural_score
            ) / denominator
    else:
        score = match_strength

    threshold = float(getattr(settings, "retrieval_coverage_threshold", 0.7))
    signals = {
        "stage": retrieval_result.get("retrieval_stage") or "merged",
        "tables": sorted(table_names),
        "metric_intent": metric_intent,
        "fact_role_tables": sorted(fact_roles),
        "relationship_count": len(relationships),
        **structural_signals,
    }
    return RetrievalCoverage(
        match_strength=round(match_strength, 4),
        structural_score=structural_score,
        score=round(score, 4),
        band="low" if score < threshold else "high",
        expanded=bool(retrieval_result.get("retrieval_coverage", {}).get("expanded", False)),
        fallback_used=False,
        signals=signals,
    )


def expand_via_graph(
    retrieval_result: dict,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> dict:
    settings = get_settings()
    max_tables = int(getattr(settings, "retrieval_expansion_max_tables", 3))
    if max_tables <= 0:
        return retrieval_result

    relationships = [
        relationship
        for relationship in _relationships(datasource_name)
        if str(relationship.fanout_risk or "").casefold() != "high"
    ]
    if not relationships:
        return retrieval_result

    allowed_tables = _allowed_tables(datasource_name)
    table_names = _retrieved_table_names(retrieval_result)
    if not table_names:
        return retrieval_result

    expanded = copy.deepcopy(retrieval_result)
    expanded["retrieval_stage"] = "expanded"
    expanded.setdefault("tables", [])
    expanded.setdefault("columns", [])

    existing_tables = {table.get("table_name") for table in expanded["tables"]}
    existing_columns = {
        (column.get("table_name"), column.get("column_name"))
        for column in expanded["columns"]
    }
    candidates: list[tuple[float, str, str, str, str, MetaRelationship]] = []
    for relationship in relationships:
        source_in = relationship.source_table in table_names
        target_in = relationship.target_table in table_names
        if source_in == target_in:
            if source_in:
                _add_column(expanded, existing_columns, relationship.source_table, relationship.source_column, relationship)
                _add_column(expanded, existing_columns, relationship.target_table, relationship.target_column, relationship)
            continue
        added_table = relationship.target_table if source_in else relationship.source_table
        if allowed_tables and added_table not in allowed_tables:
            continue
        candidates.append(
            (
                -float(relationship.confidence or 0.0),
                relationship.target_table,
                relationship.source_table,
                relationship.source_column,
                added_table,
                relationship,
            )
        )

    added_tables = 0
    for _, _, _, _, added_table, relationship in sorted(candidates):
        if added_tables >= max_tables:
            break
        if added_table not in existing_tables:
            expanded["tables"].append(
                {
                    "table_name": added_table,
                    "source": "graph_expansion",
                    "score": 0,
                    "reasons": [f"graph_expansion:{relationship.source_table}->{relationship.target_table}"],
                }
            )
            existing_tables.add(added_table)
            added_tables += 1
        _add_column(expanded, existing_columns, relationship.source_table, relationship.source_column, relationship)
        _add_column(expanded, existing_columns, relationship.target_table, relationship.target_column, relationship)

    coverage = dict(expanded.get("retrieval_coverage") or {})
    coverage["expanded"] = added_tables > 0 or bool(coverage.get("expanded"))
    coverage["expanded_tables"] = [
        table["table_name"]
        for table in expanded["tables"]
        if table.get("source") == "graph_expansion"
    ]
    expanded["retrieval_coverage"] = coverage
    return expanded


def is_empty_retrieval(retrieval_result: dict) -> bool:
    if retrieval_result.get("fallback_used"):
        return True
    return not any(
        (
            retrieval_result.get("tables"),
            retrieval_result.get("columns"),
            retrieval_result.get("metrics"),
            retrieval_result.get("verified_queries"),
        )
    )


def full_schema_fits_budget(full_schema_context: str) -> bool:
    budget = int(getattr(get_settings(), "retrieval_full_schema_char_budget", 120000))
    return budget > 0 and len(full_schema_context) <= budget


def _add_column(
    retrieval_result: dict,
    existing_columns: set[tuple[str | None, str | None]],
    table_name: str,
    column_name: str,
    relationship: MetaRelationship,
) -> None:
    key = (table_name, column_name)
    if key in existing_columns:
        return
    retrieval_result["columns"].append(
        {
            "table_name": table_name,
            "column_name": column_name,
            "source": "graph_expansion",
            "score": 0,
            "reasons": [f"join_key:{relationship.source_table}->{relationship.target_table}"],
            "matched_aliases": [],
        }
    )
    existing_columns.add(key)


def _match_strength(retrieval_result: dict) -> float:
    if "coverage_match_strength" in retrieval_result:
        try:
            return max(0.0, min(float(retrieval_result["coverage_match_strength"]), 1.0))
        except (TypeError, ValueError):
            pass
    scores = [
        float(item.get("score", 0.0) or 0.0)
        for key in ("tables", "columns", "metrics", "verified_queries")
        for item in retrieval_result.get(key, []) or []
    ]
    if not scores:
        return 0.0
    top = max(scores)
    if top <= 1.0:
        return max(0.0, min(top, 1.0))
    return max(0.0, min(top / MAX_LEXICAL_SCORE, 1.0))


def _retrieved_table_names(retrieval_result: dict) -> set[str]:
    names = {
        str(table["table_name"])
        for table in retrieval_result.get("tables", []) or []
        if table.get("table_name")
    }
    names.update(
        str(column["table_name"])
        for column in retrieval_result.get("columns", []) or []
        if column.get("table_name")
    )
    return names


def _structural_score(
    table_names: set[str],
    relationships: list[MetaRelationship],
    fact_roles: set[str],
    *,
    metric_intent: bool,
) -> tuple[float, dict[str, Any]]:
    if not table_names:
        return 0.0, {"join_connected": False, "has_fact_role": False}
    if not relationships:
        return 0.0, {"join_connected": False, "has_fact_role": False}

    connected = _is_join_connected(table_names, relationships)
    has_fact_role = bool(table_names & fact_roles)
    if not connected:
        score = 0.0
    elif metric_intent and has_fact_role:
        score = 1.0
    else:
        score = 0.5
    return score, {"join_connected": connected, "has_fact_role": has_fact_role}


def _is_join_connected(table_names: set[str], relationships: list[MetaRelationship]) -> bool:
    if len(table_names) <= 1:
        return True
    graph: dict[str, set[str]] = {table_name: set() for table_name in table_names}
    for relationship in relationships:
        if relationship.source_table in table_names and relationship.target_table in table_names:
            graph[relationship.source_table].add(relationship.target_table)
            graph[relationship.target_table].add(relationship.source_table)
    start = next(iter(table_names))
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for neighbor in graph[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen == table_names


def _fact_role_tables(
    relationships: list[MetaRelationship],
    allowed_tables: set[str],
    *,
    min_dim_edges: int,
) -> set[str]:
    targets_by_source: dict[str, set[str]] = {}
    for relationship in relationships:
        if relationship.relationship_type != "many_to_one":
            continue
        if allowed_tables and relationship.source_table not in allowed_tables:
            continue
        if allowed_tables and relationship.target_table not in allowed_tables:
            continue
        targets_by_source.setdefault(relationship.source_table, set()).add(relationship.target_table)
    return {
        table_name
        for table_name, targets in targets_by_source.items()
        if len(targets) >= min_dim_edges
    }


def _relationships(datasource_name: str) -> list[MetaRelationship]:
    _ensure_schema()
    allowed_tables = _allowed_tables(datasource_name)
    with sqlite_session() as session:
        query = select(MetaRelationship).where(MetaRelationship.datasource == datasource_name)
        if allowed_tables:
            query = query.where(
                MetaRelationship.source_table.in_(allowed_tables),
                MetaRelationship.target_table.in_(allowed_tables),
            )
        return list(
            session.scalars(
                query.order_by(
                    MetaRelationship.source_table,
                    MetaRelationship.target_table,
                    MetaRelationship.source_column,
                )
            ).all()
        )


def _allowed_tables(datasource_name: str) -> set[str]:
    _ensure_schema()
    with sqlite_session() as session:
        analysis_space = session.scalar(
            select(MetaAnalysisSpace)
            .where(MetaAnalysisSpace.enabled.is_(True), MetaAnalysisSpace.datasource == datasource_name)
            .order_by(MetaAnalysisSpace.id)
        )
        if analysis_space is None and datasource_name == DEFAULT_DATASOURCE:
            analysis_space = session.scalar(
                select(MetaAnalysisSpace)
                .where(MetaAnalysisSpace.enabled.is_(True))
                .order_by(MetaAnalysisSpace.id)
            )
        return _parse_json_set(analysis_space.tables if analysis_space else None)


def _parse_json_set(value: str | None) -> set[str]:
    if not value:
        return set()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(parsed, list):
        return set()
    return {str(item) for item in parsed}


def _ensure_schema() -> None:
    create_metadata_schema(get_sqlite_engine())
