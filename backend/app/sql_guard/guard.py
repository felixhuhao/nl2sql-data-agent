from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from backend.app.connectors.registry import get_datasource_dialect
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.sql_guard.models import GuardResult
from backend.app.sql_guard.scope import GuardScope


MAX_RESULT_ROWS = 500
BLOCKED_COMMANDS = {
    "ALTER",
    "COPY",
    "CREATE",
    "DELETE",
    "DROP",
    "INSERT",
    "INSTALL",
    "LOAD",
    "TRUNCATE",
    "UPDATE",
}
CLICKHOUSE_BLOCKED_COMMANDS = {"SYSTEM", "KILL", "RENAME", "EXCHANGE"}
DUCKDB_BLOCKED_FUNCTIONS = {"read_csv", "read_json", "read_parquet"}
CLICKHOUSE_BLOCKED_FUNCTIONS = {"s3", "url", "hdfs", "remote", "remotesecure"}
COMMAND_RE = re.compile(r"^\s*([a-zA-Z_]+)\b")
# sqlglot support for ClickHouse INSERT INTO FUNCTION is limited, so detect it before parsing.
INSERT_INTO_FUNCTION_RE = re.compile(
    r"\binsert\s+into\s+function\s+([a-zA-Z_][A-Za-z0-9_]*)\s*\(",
    re.IGNORECASE,
)


def guard_sql(
    sql: str,
    scope: GuardScope | None = None,
    datasource_name: str = DEFAULT_DATASOURCE,
    max_result_rows: int = MAX_RESULT_ROWS,
) -> GuardResult:
    dialect = get_datasource_dialect(datasource_name)

    insert_function_result = _check_insert_into_function(sql, dialect)
    if insert_function_result is not None:
        return insert_function_result

    command_result = _check_blocked_command(sql, dialect)
    if command_result is not None:
        return command_result

    statements = _parse_statements(sql, dialect)
    if isinstance(statements, GuardResult):
        return statements

    if len(statements) != 1:
        return _reject("syntax_guard", "Only one SQL statement is allowed.")

    expression = statements[0]
    operation_result = _check_operation(expression)
    if operation_result is not None:
        return operation_result
    if not isinstance(expression, exp.Select):
        return _reject("operation_guard", "Only SELECT statements are allowed.")

    function_result = _check_functions(sql, expression, dialect)
    if function_result is not None:
        return function_result

    if scope is not None:
        scope_result = _check_scope(expression, scope)
        if scope_result is not None:
            return scope_result

    fanout_result = _check_fanout_risk(expression)
    if fanout_result is not None:
        return fanout_result

    cost_result = _apply_cost_guard(expression, max_result_rows=max_result_rows)
    if isinstance(cost_result, GuardResult):
        return cost_result
    expression, warnings = cost_result

    return GuardResult(
        allowed=True,
        stage="passed",
        normalized_sql=expression.sql(dialect=dialect),
        warnings=warnings,
    )


def _parse_statements(sql: str, dialect: str) -> list[Any] | GuardResult:
    if not sql or not sql.strip():
        return _reject("syntax_guard", "SQL is empty.")
    try:
        statements = [statement for statement in sqlglot.parse(sql, read=dialect) if statement is not None]
    except ParseError as exc:
        return _reject("syntax_guard", f"SQL parse failed: {exc}")
    if not statements:
        return _reject("syntax_guard", "SQL is empty.")
    return statements


def _check_blocked_command(sql: str, dialect: str) -> GuardResult | None:
    command = _leading_command(sql)
    if command in _blocked_commands(dialect):
        return _reject("operation_guard", f"{command} is not allowed.")
    return None


def _check_operation(expression: Any) -> GuardResult | None:
    if isinstance(expression, exp.Union):
        return _reject("operation_guard", "UNION is not allowed in Phase 1.")

    if not isinstance(expression, exp.Select):
        return _reject("operation_guard", "Only SELECT statements are allowed.")

    return None


def _check_insert_into_function(sql: str, dialect: str) -> GuardResult | None:
    if dialect != "clickhouse":
        return None
    match = INSERT_INTO_FUNCTION_RE.search(sql)
    if match is None:
        return None
    name = match.group(1).lower()
    if name in CLICKHOUSE_BLOCKED_FUNCTIONS:
        return _reject("function_guard", f"INSERT INTO FUNCTION {name} is not allowed.")
    return _reject("function_guard", "INSERT INTO FUNCTION is not allowed.")


def _check_functions(sql: str, expression: Any, dialect: str) -> GuardResult | None:
    blocked_functions = _blocked_functions(dialect)
    function_re = _blocked_function_re(blocked_functions)
    match = function_re.search(sql) if function_re is not None else None
    if match is not None:
        name = match.group(1).lower()
        return _reject("function_guard", f"{name} is not allowed.")

    for function in expression.find_all(exp.Func):
        name = function.sql_name().lower()
        if name in blocked_functions:
            return _reject("function_guard", f"{name} is not allowed.")
    for table in expression.find_all(exp.Table):
        name = table.name.lower()
        if name in blocked_functions:
            return _reject("function_guard", f"{name} is not allowed.")
    return None


def _blocked_commands(dialect: str) -> set[str]:
    if dialect == "clickhouse":
        return BLOCKED_COMMANDS | CLICKHOUSE_BLOCKED_COMMANDS
    return BLOCKED_COMMANDS


def _blocked_functions(dialect: str) -> set[str]:
    if dialect == "clickhouse":
        return CLICKHOUSE_BLOCKED_FUNCTIONS
    return DUCKDB_BLOCKED_FUNCTIONS


def _blocked_function_re(blocked_functions: set[str]) -> re.Pattern | None:
    if not blocked_functions:
        return None
    pattern = "|".join(sorted(re.escape(name) for name in blocked_functions))
    return re.compile(rf"\b({pattern})\s*\(", re.IGNORECASE)


def _check_scope(expression: Any, scope: GuardScope) -> GuardResult | None:
    cte_names = _cte_names(expression)
    selects = list(expression.find_all(exp.Select))

    for select in selects:
        physical_aliases, cte_aliases = _select_table_context(select, cte_names)
        derived_sources = _derived_source_columns(select)
        referenced_tables = set(physical_aliases.values())
        projection_aliases = _projection_aliases(select)

        for table_name in sorted(referenced_tables):
            if table_name not in scope.allowed_tables:
                return _reject("scope_guard", f"Table {table_name} is not allowed.")

        for column in select.find_all(exp.Column):
            if _nearest_select(column) is not select:
                continue
            result = _check_column_scope(
                column,
                physical_aliases,
                cte_aliases,
                derived_sources,
                referenced_tables,
                projection_aliases,
                scope,
            )
            if result is not None:
                return result

    return None


def _check_fanout_risk(expression: Any) -> GuardResult | None:
    for select in expression.find_all(exp.Select):
        physical_aliases, _ = _select_table_context(select, _cte_names(expression))
        referenced_tables = set(physical_aliases.values())
        if {"fact_orders", "fact_order_items"}.issubset(referenced_tables) and _select_aggregates_order_amount(select):
            return _reject(
                "fanout_guard",
                "Aggregating fact_orders.payment_amount after joining fact_order_items can inflate sales amount.",
            )
    return None


def _select_aggregates_order_amount(select: exp.Select) -> bool:
    for aggregate in select.find_all(exp.AggFunc):
        for column in aggregate.find_all(exp.Column):
            if column.name == "payment_amount" and _nearest_select(column) is select:
                return True
    return False


def _apply_cost_guard(
    expression: exp.Select,
    *,
    max_result_rows: int = MAX_RESULT_ROWS,
) -> tuple[exp.Select, list[str]] | GuardResult:
    limit = expression.args.get("limit")
    if limit is None:
        if _is_scalar_aggregate_select(expression):
            return expression, []
        return (
            expression.limit(max_result_rows),
            [f"LIMIT {max_result_rows} was added automatically."],
        )

    limit_value = _limit_value(limit)
    if limit_value is None:
        return _reject("cost_guard", "LIMIT must be an integer literal.")
    if limit_value < 0:
        return _reject("cost_guard", "LIMIT must be non-negative.")
    if limit_value > max_result_rows:
        limit.set("expression", exp.Literal.number(max_result_rows))
        return (
            expression,
            [f"LIMIT {limit_value} was capped to {max_result_rows}."],
        )

    return expression, []


def _limit_value(limit: exp.Limit) -> int | None:
    limit_expression = limit.args.get("expression")
    if isinstance(limit_expression, exp.Neg):
        positive_value = _literal_int(limit_expression.this)
        if positive_value is None:
            return None
        return -positive_value
    return _literal_int(limit_expression)


def _literal_int(expression: Any | None) -> int | None:
    if not isinstance(expression, exp.Literal) or expression.is_string:
        return None
    try:
        return int(expression.this)
    except (TypeError, ValueError):
        return None


def _is_scalar_aggregate_select(expression: exp.Select) -> bool:
    if expression.args.get("group") is not None:
        return False
    aggregates = [aggregate for aggregate in expression.find_all(exp.AggFunc) if _nearest_select(aggregate) is expression]
    if not aggregates:
        return False
    for column in expression.find_all(exp.Column):
        if _nearest_select(column) is expression and _nearest_aggregate(column) is None:
            return False
    return True


def _nearest_aggregate(expression: Any) -> exp.AggFunc | None:
    current = expression.parent
    while current is not None:
        if isinstance(current, exp.AggFunc):
            return current
        if isinstance(current, exp.Select):
            return None
        current = current.parent
    return None


def _cte_names(expression: Any) -> set[str]:
    return {cte.alias_or_name for cte in expression.find_all(exp.CTE)}


def _select_table_context(select: exp.Select, cte_names: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    physical_aliases: dict[str, str] = {}
    cte_aliases: dict[str, str] = {}
    for table in select.find_all(exp.Table):
        if _nearest_select(table) is not select:
            continue
        table_name = table.name
        aliases = cte_aliases if table_name in cte_names else physical_aliases
        aliases[table_name] = table_name
        aliases[table.alias_or_name] = table_name
    return physical_aliases, cte_aliases


def _nearest_select(expression: Any) -> exp.Select | None:
    current = expression
    while current is not None:
        if isinstance(current, exp.Select):
            return current
        current = current.parent
    return None


def _projection_aliases(select: exp.Select) -> set[str]:
    aliases: set[str] = set()
    for projection in select.expressions:
        alias = projection.alias
        if alias:
            aliases.add(alias)
    return aliases


def _derived_source_columns(select: exp.Select) -> dict[str, set[str]]:
    sources: dict[str, set[str]] = {}
    for subquery in select.find_all(exp.Subquery):
        if _nearest_select(subquery) is not select:
            continue
        if not isinstance(subquery.parent, exp.From | exp.Join):
            continue
        if not isinstance(subquery.this, exp.Select):
            continue
        columns = _select_output_columns(subquery.this)
        if not columns:
            continue
        sources[""] = sources.get("", set()) | columns
        alias = subquery.alias_or_name
        if alias:
            sources[alias] = columns
    return sources


def _select_output_columns(select: exp.Select) -> set[str]:
    columns: set[str] = set()
    for projection in select.expressions:
        alias = projection.alias
        if alias:
            columns.add(alias)
        elif isinstance(projection, exp.Column):
            columns.add(projection.name)
    return columns


def _check_column_scope(
    column: exp.Column,
    physical_aliases: dict[str, str],
    cte_aliases: dict[str, str],
    derived_sources: dict[str, set[str]],
    referenced_tables: set[str],
    projection_aliases: set[str],
    scope: GuardScope,
) -> GuardResult | None:
    column_name = column.name
    if column_name == "*":
        return _check_star_scope(column, physical_aliases, cte_aliases)

    qualifier = column.table
    if qualifier:
        physical_table_name = physical_aliases.get(qualifier)
        if physical_table_name is not None:
            if column_name not in scope.columns_for_table(physical_table_name):
                return _reject("scope_guard", f"Column {physical_table_name}.{column_name} is not allowed.")
            return None
        if qualifier in cte_aliases:
            return None
        if qualifier in derived_sources:
            if column_name in derived_sources[qualifier]:
                return None
            return _reject("scope_guard", f"Column {qualifier}.{column_name} is not allowed.")
        if qualifier not in physical_aliases:
            return _reject("scope_guard", f"Unknown table qualifier {qualifier}.")

    if column_name in projection_aliases:
        return None
    if column_name in derived_sources.get("", set()):
        return None

    matching_tables = [
        table_name
        for table_name in sorted(referenced_tables)
        if column_name in scope.columns_for_table(table_name)
    ]
    if len(matching_tables) == 1:
        return None
    if len(matching_tables) > 1:
        return _reject("scope_guard", f"Column {column_name} is ambiguous.")
    if cte_aliases:
        return None
    return _reject("scope_guard", f"Column {column_name} is not allowed.")


def _check_star_scope(
    column: exp.Column,
    physical_aliases: dict[str, str],
    cte_aliases: dict[str, str],
) -> GuardResult | None:
    qualifier = column.table
    if qualifier and qualifier not in physical_aliases and qualifier not in cte_aliases:
        return _reject("scope_guard", f"Unknown table qualifier {qualifier}.")
    return None


def _leading_command(sql: str) -> str | None:
    match = COMMAND_RE.match(_strip_leading_comments(sql))
    if match is None:
        return None
    return match.group(1).upper()


def _strip_leading_comments(sql: str) -> str:
    remaining = sql.lstrip()
    while True:
        if remaining.startswith("--"):
            newline_index = remaining.find("\n")
            if newline_index == -1:
                return ""
            remaining = remaining[newline_index + 1 :].lstrip()
            continue

        if remaining.startswith("/*"):
            comment_end = remaining.find("*/", 2)
            if comment_end == -1:
                return remaining
            remaining = remaining[comment_end + 2 :].lstrip()
            continue

        return remaining


def _reject(stage: str, reason: str) -> GuardResult:
    return GuardResult(allowed=False, stage=stage, reason=reason)
