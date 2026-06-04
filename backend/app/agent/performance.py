from __future__ import annotations

import logging
import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from backend.app.agent.state import AgentState
from backend.app.connectors.registry import get_datasource_manager
from backend.app.metadata.semantic_overlay import TABLE_SEMANTICS

logger = logging.getLogger(__name__)

_PARTS_RE = re.compile(r"Parts:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
_JOIN_RE = re.compile(r"\b(?:[A-Za-z]*Join[A-Za-z]*|JoiningTransform)\b", re.IGNORECASE)
_DATE_FILTER_RE = re.compile(r"\b(date_key|date_value|order_date|sale_date|created_at)\b", re.IGNORECASE)
_FACT_TABLES = frozenset(
    table_name
    for table_name, (_, _, domain) in TABLE_SEMANTICS.items()
    if table_name.startswith("fact_") and domain == "sales"
) or frozenset({"fact_orders", "fact_order_items"})


def explain_performance_node(state: AgentState) -> AgentState:
    state.plan_hints = []
    state.runtime_stats = _runtime_stats(state)

    if state.execution_error or state.query_result is None:
        return state

    if state.datasource_dialect != "clickhouse":
        return state

    normalized_sql = state.guard_result.normalized_sql if state.guard_result else None
    if not normalized_sql:
        state.completed_steps.append("explain_plan")
        return state

    explain_result = state.query_result.explain_plan
    if explain_result is None:
        try:
            explain_result = get_datasource_manager().get(state.datasource_name).explain(normalized_sql)
        except Exception as exc:
            logger.warning("ClickHouse EXPLAIN failed for %s: %s", state.datasource_name, exc)
            state.completed_steps.append("explain_plan")
            return state

    if explain_result is not None:
        state.plan_hints = parse_plan_hints(
            explain_result,
            matched_tables=(state.explainability or {}).get("matched_tables", []),
            sql=normalized_sql,
        )

    state.completed_steps.append("explain_plan")
    return state


def parse_plan_hints(
    explain_result: dict[str, Any],
    matched_tables: list[str] | None = None,
    sql: str = "",
) -> list[str]:
    lines = _explain_lines(explain_result)
    text = "\n".join(lines)
    if not text:
        return []

    hints: list[str] = []
    parts_match = _PARTS_RE.search(text)
    if parts_match:
        scanned = int(parts_match.group(1))
        total = int(parts_match.group(2))
        if total == 0:
            hints.append("查询计划显示没有可扫描的 parts。")
        elif scanned < total:
            hints.append(f"命中分区裁剪，扫描 {scanned}/{total} parts。")
        else:
            hints.append(f"未明显命中分区裁剪，扫描 {scanned}/{total} parts。")
    elif "partition" in text.casefold():
        hints.append("查询计划包含分区过滤信息。")

    if _uses_sorting_key(text):
        hints.append("查询计划包含排序键相关读取。")

    join_count = _join_count(lines)
    if join_count:
        hints.append(f"包含 {join_count} 个 JOIN。")

    if _should_suggest_time_filter(sql=sql, matched_tables=matched_tables or []):
        hints.append("建议添加明确的时间范围过滤以减少 ClickHouse 扫描。")

    if not hints:
        hints.append("EXPLAIN 已生成，未发现明显性能风险。")
    return hints


def _runtime_stats(state: AgentState) -> dict | None:
    if state.datasource_dialect != "clickhouse" or state.query_result is None:
        return None
    if state.query_result.elapsed_ms is None:
        return None
    return {"execution_time_ms": state.query_result.elapsed_ms}


def _explain_lines(explain_result: dict[str, Any]) -> list[str]:
    raw_lines = explain_result.get("lines")
    if isinstance(raw_lines, list):
        return [str(line) for line in raw_lines if str(line).strip()]
    text = explain_result.get("text", "")
    return [line for line in str(text).splitlines() if line.strip()]


def _uses_sorting_key(plan_text: str) -> bool:
    lower_text = plan_text.casefold()
    return any(marker in lower_text for marker in ("sortingkey", "sorting key", "readinorder", "primarykey"))


def _join_count(lines: list[str]) -> int:
    return sum(1 for line in lines if _JOIN_RE.search(line))


def _should_suggest_time_filter(sql: str, matched_tables: list[str]) -> bool:
    lower_sql = sql.casefold()
    references_fact_table = bool(_FACT_TABLES & {table.casefold() for table in matched_tables}) or any(
        table in lower_sql for table in _FACT_TABLES
    )
    if not references_fact_table:
        return False

    return not _has_time_filter(sql)


def _has_time_filter(sql: str) -> bool:
    try:
        expression = sqlglot.parse_one(sql, read="clickhouse")
    except ParseError:
        return _DATE_FILTER_RE.search(sql) is not None

    for where in expression.find_all(exp.Where):
        if any(_DATE_FILTER_RE.fullmatch(column.name) for column in where.find_all(exp.Column)):
            return True
    return False
