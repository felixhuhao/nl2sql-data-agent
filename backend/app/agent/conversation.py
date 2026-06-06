from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from backend.app.agent.state import AgentState


@dataclass(frozen=True)
class FilterPredicate:
    column: str
    op: str
    value: str

    def label(self) -> str:
        return f"{self.column} {self.op} {self.value}"


@dataclass(frozen=True)
class ConversationContext:
    question: str
    normalized_sql: str
    datasource_name: str
    matched_tables: list[str] = field(default_factory=list)
    matched_columns: list[str] = field(default_factory=list)
    join_paths: list[dict[str, Any]] = field(default_factory=list)
    metric: str | None = None
    date_interpretation: dict[str, Any] | None = None
    result_columns: list[str] = field(default_factory=list)
    active_filters: list[FilterPredicate] = field(default_factory=list)


def build_conversation_context(state: AgentState) -> ConversationContext | None:
    if state.guard_result is None or not state.guard_result.allowed or not state.guard_result.normalized_sql:
        return None
    if state.query_result is None:
        return None

    active_filters = _where_filter_predicates(
        state.guard_result.normalized_sql,
        dialect=state.datasource_dialect,
    )
    explainability = state.explainability or {}
    return ConversationContext(
        question=state.question,
        normalized_sql=state.guard_result.normalized_sql,
        datasource_name=state.datasource_name,
        matched_tables=list(explainability.get("matched_tables") or []),
        matched_columns=list(explainability.get("matched_columns") or []),
        join_paths=list(explainability.get("join_paths") or []),
        metric=_metric_from_retrieval(state.retrieval_result),
        date_interpretation=explainability.get("date_interpretation"),
        result_columns=list(state.query_result.columns),
        active_filters=active_filters,
    )


def merge_prior_assets_into_retrieval(retrieval_result: dict, context: ConversationContext | None) -> dict:
    if context is None:
        return retrieval_result

    merged = {
        **retrieval_result,
        "tables": list(retrieval_result.get("tables", [])),
        "columns": list(retrieval_result.get("columns", [])),
    }
    table_names = {table.get("table_name") for table in merged["tables"]}
    for table_name in context.matched_tables:
        if table_name and table_name not in table_names:
            merged["tables"].append({"table_name": table_name, "source": "conversation_context"})
            table_names.add(table_name)

    column_keys = {
        (column.get("table_name"), column.get("column_name"))
        for column in merged["columns"]
    }
    for qualified_column in context.matched_columns:
        table_name, column_name = _split_qualified_column(qualified_column)
        if not table_name or not column_name:
            continue
        key = (table_name, column_name)
        if key not in column_keys:
            merged["columns"].append(
                {
                    "table_name": table_name,
                    "column_name": column_name,
                    "source": "conversation_context",
                }
            )
            column_keys.add(key)
    return merged


def conversation_context_prompt(context: ConversationContext | None) -> str:
    if context is None:
        return ""

    lines = [
        "Previous question:",
        context.question,
        "",
        "Previous Guard-approved SQL:",
        context.normalized_sql,
        "",
        "Previous assets:",
        f"- tables: {', '.join(context.matched_tables) or '(none)'}",
        f"- columns: {', '.join(context.matched_columns) or '(none)'}",
        f"- result_columns: {', '.join(context.result_columns) or '(none)'}",
        f"- join_paths: {context.join_paths or '(none)'}",
    ]
    if context.metric:
        lines.append(f"- metric: {context.metric}")
    if context.date_interpretation:
        lines.append(f"- date_interpretation: {context.date_interpretation}")
    if context.active_filters:
        lines.extend(
            [
                "",
                "Preserve these filters unless the user explicitly changes filters:",
                *[f"- {predicate.label()}" for predicate in context.active_filters],
            ]
        )
    return "\n".join(lines)


def missing_carried_filters(state: AgentState) -> list[FilterPredicate]:
    if not state.is_follow_up:
        return []
    if state.conversation_context is None or not state.conversation_context.active_filters:
        return []
    if state.change_kind == "filter":
        return []
    if state.guard_result is None or not state.guard_result.allowed or not state.guard_result.normalized_sql:
        return []

    sql_filters = _where_filter_predicates(
        state.guard_result.normalized_sql,
        dialect=state.datasource_dialect,
    )
    missing = []
    for predicate in state.conversation_context.active_filters:
        if state.change_kind == "time" and _is_time_filter(predicate):
            continue
        if not _contains_filter(sql_filters, predicate):
            missing.append(predicate)
    return missing


def _where_filter_predicates(sql: str, dialect: str) -> list[FilterPredicate]:
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except ParseError:
        return []
    where = expression.args.get("where")
    if where is None:
        return []

    aliases = _table_aliases(expression)
    predicates: list[FilterPredicate] = []
    for node in where.walk():
        if isinstance(node, exp.EQ):
            predicate = _binary_predicate(node, aliases)
            if predicate is not None:
                predicates.append(predicate)
        elif isinstance(node, exp.In):
            predicates.extend(_in_predicates(node, aliases))
    return _dedupe_filters(predicates)


def _binary_predicate(node: exp.EQ, aliases: dict[str, str]) -> FilterPredicate | None:
    column = _qualified_column(node.left, aliases)
    value = _literal_value(node.right)
    if column is None or value is None:
        return None
    return FilterPredicate(column=column, op="=", value=value)


def _in_predicates(node: exp.In, aliases: dict[str, str]) -> list[FilterPredicate]:
    column = _qualified_column(node.this, aliases)
    if column is None:
        return []
    predicates = []
    for item in node.expressions:
        value = _literal_value(item)
        if value is not None:
            predicates.append(FilterPredicate(column=column, op="in", value=value))
    return predicates


def _qualified_column(expression: Any, aliases: dict[str, str]) -> str | None:
    if not isinstance(expression, exp.Column):
        return None
    table_name = aliases.get(expression.table, expression.table)
    if not table_name:
        return expression.name
    return f"{table_name}.{expression.name}"


def _literal_value(expression: Any) -> str | None:
    if isinstance(expression, exp.Literal):
        return str(expression.this)
    return None


def _table_aliases(expression: Any) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        table_name = table.name
        aliases[table_name] = table_name
        aliases[table.alias_or_name] = table_name
    return aliases


def _contains_filter(filters: list[FilterPredicate], predicate: FilterPredicate) -> bool:
    return any(
        item.column == predicate.column
        and item.value == predicate.value
        and (item.op == predicate.op or {item.op, predicate.op} <= {"=", "in"})
        for item in filters
    )


def _is_time_filter(predicate: FilterPredicate) -> bool:
    column = predicate.column.casefold()
    return any(token in column for token in ("date", "time", "year", "month", "quarter", "week", "day"))


def _dedupe_filters(predicates: list[FilterPredicate]) -> list[FilterPredicate]:
    seen = set()
    result = []
    for predicate in predicates:
        key = (predicate.column, predicate.op, predicate.value)
        if key in seen:
            continue
        seen.add(key)
        result.append(predicate)
    return result


def _metric_from_retrieval(retrieval_result: dict | None) -> str | None:
    metrics = (retrieval_result or {}).get("metrics") or []
    if not metrics:
        return None
    return metrics[0].get("name")


def _split_qualified_column(value: str) -> tuple[str | None, str | None]:
    if "." not in value:
        return None, None
    table_name, column_name = value.split(".", 1)
    return table_name, column_name
