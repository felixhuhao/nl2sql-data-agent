import time
from typing import Any

from pydantic import BaseModel

from backend.app.connectors.registry import get_datasource_manager
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.sql_guard.models import GuardResult


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    elapsed_ms: float | None = None


def execute_guarded_sql(
    guard_result: GuardResult,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> QueryResult:
    if not guard_result.allowed:
        raise ValueError(f"SQL was rejected by {guard_result.stage}: {guard_result.reason}")
    if guard_result.normalized_sql is None:
        raise ValueError("Guard result does not include normalized_sql.")

    started_at = time.perf_counter()
    raw_result = get_datasource_manager().get(datasource_name).execute(guard_result.normalized_sql)
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
    return QueryResult(
        columns=raw_result.columns,
        rows=raw_result.rows,
        row_count=raw_result.row_count,
        elapsed_ms=elapsed_ms,
    )
