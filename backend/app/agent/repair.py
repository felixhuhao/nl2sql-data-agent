from __future__ import annotations

from backend.app.agent.state import AgentState
from backend.app.sql_guard.models import GuardResult


REPAIRABLE_GUARD_STAGES = frozenset(
    {
        "scope_guard",
        "syntax_guard",
        "function_guard",
        "fanout_guard",
        "cost_guard",
    }
)
NON_REPAIRABLE_GUARD_STAGES = frozenset({"operation_guard"})

_INFRASTRUCTURE_ERROR_TOKENS = (
    "connection",
    "connect",
    "timeout",
    "timed out",
    "outofmemory",
    "out of memory",
    "memoryerror",
)
_REPAIRABLE_EXECUTION_ERROR_TOKENS = (
    "parser",
    "catalog",
    "binder",
    "invalidinput",
    "invalid input",
)


def is_guard_repairable(guard_result: GuardResult | None) -> bool:
    return bool(guard_result and guard_result.stage in REPAIRABLE_GUARD_STAGES)


def is_execution_repairable(error: Exception) -> bool:
    error_text = f"{type(error).__name__} {error}".casefold()
    if any(token in error_text for token in _INFRASTRUCTURE_ERROR_TOKENS):
        return False
    return any(token in error_text for token in _REPAIRABLE_EXECUTION_ERROR_TOKENS)


def reset_failure_state(state: AgentState) -> None:
    state.error = None
    state.stopped_at = None
    state.guard_result = None
    state.query_result = None
    state.summary = None
    state.explainability = None
    state.execution_error = None
