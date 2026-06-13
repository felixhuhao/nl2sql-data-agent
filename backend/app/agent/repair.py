from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from backend.app.agent.conversation import missing_carried_filters
from backend.app.agent.nodes import ScopeBuilder, SQLExecutor, execute_node, repair_sql_node, sql_guard_node
from backend.app.agent.semantic_grounding import (
    SemanticGroundingVerifier,
    SemanticRefutationAuditor,
    semantic_guard_node,
    semantic_guard_mode_value,
)
from backend.app.agent.state import AgentState
from backend.app.core.llm_provider import LLMProvider, SQLRepairContext
from backend.app.sql_guard.models import GuardResult


MAX_REPAIRS = 2
REPAIRABLE_GUARD_STAGES = frozenset(
    {
        "scope_guard",
        "syntax_guard",
        "function_guard",
        "fanout_guard",
        "cost_guard",
    }
)
NON_REPAIRABLE_GUARD_STAGES = frozenset({"operation_guard", "semantic_guard"})

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


@dataclass(frozen=True)
class RepairEvent:
    step: str
    state: AgentState
    attempt: int | None = None
    error: Exception | None = None
    error_stage: str | None = None
    error_kind: str | None = None
    error_reason: str | None = None


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
    state.plan_hints = []
    state.runtime_stats = None
    state.semantic_guard_result = None
    state.grounding_warnings = []


def iter_sql_repair_events(
    state: AgentState,
    *,
    provider: LLMProvider,
    scope_builder: ScopeBuilder,
    executor: SQLExecutor,
    semantic_verifier: SemanticGroundingVerifier | None = None,
    semantic_auditor: SemanticRefutationAuditor | None = None,
    semantic_mode: str | None = None,
    max_repairs: int = MAX_REPAIRS,
) -> Iterator[RepairEvent]:
    repair_count = 0
    active_semantic_mode = semantic_guard_mode_value(semantic_mode)

    while True:
        reset_failure_state(state)
        sql_guard_node(state, scope_builder=scope_builder)
        yield RepairEvent(step="sql_guard", state=state)

        if state.guard_result is None:
            raise ValueError("guard_result is required after SQL Guard.")

        if not state.guard_result.allowed:
            if not is_guard_repairable(state.guard_result) or repair_count >= max_repairs:
                _mark_latest_repair(state, succeeded=False, final_stage=state.guard_result.stage)
                yield _error_event_from_guard(state)
                return
            _mark_latest_repair(state, succeeded=False, final_stage=state.guard_result.stage)
            repair_count += 1
            repair_context = _repair_context_from_guard(state, attempt=repair_count)
            repair_sql_node(state, provider=provider, repair_context=repair_context)
            yield RepairEvent(step="repair_sql", state=state, attempt=repair_count)
            continue

        state.missing_carried_filters = missing_carried_filters(state)
        if state.is_follow_up and state.conversation_context is not None and state.conversation_context.active_filters:
            state.completed_steps.append("conversation_filter_verify")
            yield RepairEvent(step="conversation_filter_verify", state=state)
        if state.missing_carried_filters:
            if repair_count >= max_repairs:
                state.stopped_at = "conversation_filter_verify"
                state.error = _missing_filter_reason(state)
                _mark_latest_repair(state, succeeded=False, final_stage="conversation_filter_verify")
                yield RepairEvent(
                    step="error",
                    state=state,
                    error_stage="conversation_filter_verify",
                    error_kind="missing_carried_filter",
                    error_reason=state.error,
                )
                return
            _mark_latest_repair(state, succeeded=False, final_stage="conversation_filter_verify")
            repair_count += 1
            repair_context = _repair_context_from_missing_filter(state, attempt=repair_count)
            repair_sql_node(state, provider=provider, repair_context=repair_context)
            yield RepairEvent(step="repair_sql", state=state, attempt=repair_count)
            continue

        if active_semantic_mode != "off":
            semantic_guard_node(
                state,
                verifier=semantic_verifier,
                auditor=semantic_auditor,
                mode=active_semantic_mode,
            )
            if state.stopped_at == "semantic_guard":
                _mark_latest_repair(state, succeeded=False, final_stage="semantic_guard")
                yield RepairEvent(
                    step="error",
                    state=state,
                    error_stage="semantic_guard",
                    error_kind="semantic_guard",
                    error_reason=state.error,
                )
                return
            yield RepairEvent(step="semantic_guard", state=state)

        try:
            execute_node(state, executor=executor)
        except Exception as exc:
            state.execution_error = str(exc)
            if not is_execution_repairable(exc) or repair_count >= max_repairs:
                state.stopped_at = "execute"
                state.error = str(exc)
                _mark_latest_repair(state, succeeded=False, final_stage="execute")
                yield RepairEvent(
                    step="error",
                    state=state,
                    error=exc,
                    error_stage="execute",
                    error_kind=type(exc).__name__,
                    error_reason=str(exc),
                )
                return
            _mark_latest_repair(state, succeeded=False, final_stage="execute")
            repair_count += 1
            repair_context = _repair_context_from_execution(state, exc, attempt=repair_count)
            repair_sql_node(state, provider=provider, repair_context=repair_context)
            yield RepairEvent(step="repair_sql", state=state, attempt=repair_count)
            continue

        _mark_latest_repair(state, succeeded=True, final_stage="execute")
        yield RepairEvent(step="execute", state=state)
        return


def _repair_context_from_guard(state: AgentState, attempt: int) -> SQLRepairContext:
    if state.sql is None:
        raise ValueError("sql is required for SQL repair.")
    if state.guard_result is None:
        raise ValueError("guard_result is required for SQL repair.")
    return SQLRepairContext(
        attempt=attempt,
        original_sql=state.sql,
        error_stage="sql_guard",
        error_kind=state.guard_result.stage,
        error_reason=state.guard_result.reason or "",
        normalized_sql=state.guard_result.normalized_sql,
    )


def _repair_context_from_execution(
    state: AgentState,
    error: Exception,
    attempt: int,
) -> SQLRepairContext:
    if state.sql is None:
        raise ValueError("sql is required for SQL repair.")
    normalized_sql = state.guard_result.normalized_sql if state.guard_result else None
    return SQLRepairContext(
        attempt=attempt,
        original_sql=state.sql,
        error_stage="execute",
        error_kind=type(error).__name__,
        error_reason=str(error),
        normalized_sql=normalized_sql,
    )


def _repair_context_from_missing_filter(state: AgentState, attempt: int) -> SQLRepairContext:
    if state.sql is None:
        raise ValueError("sql is required for SQL repair.")
    normalized_sql = state.guard_result.normalized_sql if state.guard_result else None
    return SQLRepairContext(
        attempt=attempt,
        original_sql=state.sql,
        error_stage="conversation_filter_verify",
        error_kind="missing_carried_filter",
        error_reason=_missing_filter_reason(state),
        normalized_sql=normalized_sql,
    )


def _missing_filter_reason(state: AgentState) -> str:
    filters = ", ".join(predicate.label() for predicate in state.missing_carried_filters)
    return f"Follow-up SQL dropped carried filter(s): {filters}"


def _error_event_from_guard(state: AgentState) -> RepairEvent:
    guard_result = state.guard_result
    return RepairEvent(
        step="error",
        state=state,
        error_stage="sql_guard",
        error_kind=guard_result.stage if guard_result else None,
        error_reason=guard_result.reason if guard_result else None,
    )


def _mark_latest_repair(state: AgentState, *, succeeded: bool, final_stage: str) -> None:
    if not state.repair_history:
        return
    state.repair_history[-1]["succeeded"] = succeeded
    state.repair_history[-1]["final_stage"] = final_stage
