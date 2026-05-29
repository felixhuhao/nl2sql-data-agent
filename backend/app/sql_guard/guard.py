from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from backend.app.sql_guard.models import GuardResult


DIALECT = "duckdb"
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
BLOCKED_FUNCTIONS = {"read_csv", "read_json", "read_parquet"}
COMMAND_RE = re.compile(r"^\s*([a-zA-Z_]+)\b")
BLOCKED_FUNCTION_RE = re.compile(
    r"\b(read_csv|read_json|read_parquet)\s*\(",
    re.IGNORECASE,
)


def guard_sql(sql: str) -> GuardResult:
    statements = _parse_statements(sql)
    if isinstance(statements, GuardResult):
        return statements

    if len(statements) != 1:
        return _reject("syntax_guard", "Only one SQL statement is allowed.")

    expression = statements[0]
    operation_result = _check_operation(sql, expression)
    if operation_result is not None:
        return operation_result

    function_result = _check_functions(sql, expression)
    if function_result is not None:
        return function_result

    return GuardResult(
        allowed=True,
        stage="passed",
        normalized_sql=expression.sql(dialect=DIALECT),
    )


def _parse_statements(sql: str) -> list[exp.Expression] | GuardResult:
    if not sql or not sql.strip():
        return _reject("syntax_guard", "SQL is empty.")
    try:
        statements = [statement for statement in sqlglot.parse(sql, read=DIALECT) if statement is not None]
    except ParseError as exc:
        return _reject("syntax_guard", f"SQL parse failed: {exc}")
    if not statements:
        return _reject("syntax_guard", "SQL is empty.")
    return statements


def _check_operation(sql: str, expression: exp.Expression) -> GuardResult | None:
    command = _leading_command(sql)
    if command in BLOCKED_COMMANDS:
        return _reject("operation_guard", f"{command} is not allowed.")

    if isinstance(expression, exp.Union):
        return _reject("operation_guard", "UNION is not allowed in Phase 1.")

    if not isinstance(expression, exp.Select):
        return _reject("operation_guard", "Only SELECT statements are allowed.")

    return None


def _check_functions(sql: str, expression: exp.Expression) -> GuardResult | None:
    match = BLOCKED_FUNCTION_RE.search(sql)
    if match is not None:
        name = match.group(1).lower()
        return _reject("function_guard", f"{name} is not allowed.")

    for function in expression.find_all(exp.Func):
        name = function.sql_name().lower()
        if name in BLOCKED_FUNCTIONS:
            return _reject("function_guard", f"{name} is not allowed.")
    for table in expression.find_all(exp.Table):
        name = table.name.lower()
        if name in BLOCKED_FUNCTIONS:
            return _reject("function_guard", f"{name} is not allowed.")
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
