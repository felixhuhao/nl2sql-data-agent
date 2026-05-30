from typing import Any

from pydantic import BaseModel

from backend.app.core.db import get_duckdb_connection
from backend.app.sql_guard.models import GuardResult


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int


def execute_guarded_sql(guard_result: GuardResult) -> QueryResult:
    if not guard_result.allowed:
        raise ValueError(f"SQL was rejected by {guard_result.stage}: {guard_result.reason}")
    if guard_result.normalized_sql is None:
        raise ValueError("Guard result does not include normalized_sql.")

    connection = get_duckdb_connection(read_only=True)
    try:
        cursor = connection.execute(guard_result.normalized_sql)
        rows = [list(row) for row in cursor.fetchall()]
        columns = [column[0] for column in cursor.description or []]
        return QueryResult(columns=columns, rows=rows, row_count=len(rows))
    finally:
        connection.close()
