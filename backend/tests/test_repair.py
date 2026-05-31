from backend.app.agent.repair import (
    is_execution_repairable,
    is_guard_repairable,
    reset_failure_state,
)
from backend.app.agent.state import AgentState
from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult


def test_is_guard_repairable_accepts_repairable_stages():
    for stage in ("scope_guard", "syntax_guard", "function_guard", "fanout_guard", "cost_guard"):
        assert is_guard_repairable(GuardResult(allowed=False, stage=stage, reason="failed"))


def test_is_guard_repairable_rejects_operation_guard_and_missing_result():
    assert not is_guard_repairable(None)
    assert not is_guard_repairable(
        GuardResult(allowed=False, stage="operation_guard", reason="DELETE is not allowed.")
    )


def test_is_execution_repairable_accepts_duckdb_error_classes():
    class CatalogException(Exception):
        pass

    class BinderException(Exception):
        pass

    class ParserException(Exception):
        pass

    assert is_execution_repairable(CatalogException("Table does not exist"))
    assert is_execution_repairable(BinderException("Column not found"))
    assert is_execution_repairable(ParserException("syntax error"))
    assert is_execution_repairable(ValueError("Invalid Input Error: bad function argument"))


def test_is_execution_repairable_rejects_infrastructure_errors():
    assert not is_execution_repairable(TimeoutError("read operation timed out"))
    assert not is_execution_repairable(ConnectionError("connection refused"))
    assert not is_execution_repairable(MemoryError("out of memory"))


def test_reset_failure_state_clears_transient_failure_fields():
    state = AgentState(
        question="test",
        guard_result=GuardResult(allowed=False, stage="scope_guard", reason="bad column"),
        query_result=QueryResult(columns=["x"], rows=[[1]], row_count=1),
        summary="summary",
        explainability={"guard_result": {"allowed": False}},
        execution_error="CatalogException",
        error="bad column",
        stopped_at="sql_guard",
    )

    reset_failure_state(state)

    assert state.error is None
    assert state.stopped_at is None
    assert state.guard_result is None
    assert state.query_result is None
    assert state.summary is None
    assert state.explainability is None
    assert state.execution_error is None
