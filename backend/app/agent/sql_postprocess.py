from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


DIALECT = "duckdb"
SALES_AMOUNT_COLUMNS = {
    ("fact_orders", "payment_amount"),
    ("fact_order_items", "item_amount"),
}


def normalize_generated_sql(sql: str) -> str:
    """Apply deterministic cleanup to LLM SQL before governance."""
    try:
        statements = [statement for statement in sqlglot.parse(sql, read=DIALECT) if statement is not None]
    except ParseError:
        return sql

    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return sql

    expression = statements[0]
    if _alias_metric_projections(expression):
        return expression.sql(dialect=DIALECT)
    return sql


def _alias_metric_projections(select: exp.Select) -> bool:
    table_aliases = _table_aliases(select)
    projections = []
    changed = False
    for projection in select.expressions:
        if projection.alias:
            projections.append(projection)
            continue

        alias = _metric_alias_for_projection(projection, table_aliases)
        if alias is None:
            projections.append(projection)
            continue

        projections.append(exp.alias_(projection.copy(), alias, copy=False))
        changed = True

    if changed:
        select.set("expressions", projections)
    return changed


def _metric_alias_for_projection(projection: Any, table_aliases: dict[str, str]) -> str | None:
    if _is_aov_expression(projection, table_aliases):
        return "aov"
    if _is_sales_amount_expression(projection, table_aliases):
        return "sales_amount"
    if _is_order_count_expression(projection, table_aliases):
        return "order_count"
    return None


def _is_sales_amount_expression(expression: Any, table_aliases: dict[str, str]) -> bool:
    if not isinstance(expression, exp.Sum):
        return False
    column = expression.this
    return isinstance(column, exp.Column) and any(
        _column_matches(column, table_name, column_name, table_aliases)
        for table_name, column_name in SALES_AMOUNT_COLUMNS
    )


def _is_order_count_expression(expression: Any, table_aliases: dict[str, str]) -> bool:
    if not isinstance(expression, exp.Count):
        return False
    distinct = expression.this
    if not isinstance(distinct, exp.Distinct) or len(distinct.expressions) != 1:
        return False
    column = distinct.expressions[0]
    return isinstance(column, exp.Column) and _column_matches(column, "fact_orders", "order_id", table_aliases)


def _is_aov_expression(expression: Any, table_aliases: dict[str, str]) -> bool:
    if not isinstance(expression, exp.Div):
        return False
    return _is_sales_amount_expression(expression.left, table_aliases) and _is_order_count_expression(
        expression.right,
        table_aliases,
    )


def _column_matches(
    column: exp.Column,
    table_name: str,
    column_name: str,
    table_aliases: dict[str, str],
) -> bool:
    if column.name != column_name:
        return False

    qualifier = column.table
    if qualifier:
        return table_aliases.get(qualifier) == table_name

    physical_tables = set(table_aliases.values())
    return physical_tables == {table_name}


def _table_aliases(select: exp.Select) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in select.find_all(exp.Table):
        if _nearest_select(table) is not select:
            continue
        aliases[table.name] = table.name
        aliases[table.alias_or_name] = table.name
    return aliases


def _nearest_select(expression: Any) -> exp.Select | None:
    current = expression
    while current is not None:
        if isinstance(current, exp.Select):
            return current
        current = current.parent
    return None
