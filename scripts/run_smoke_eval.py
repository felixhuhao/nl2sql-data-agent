from __future__ import annotations

import argparse
import copy
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

import httpx
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

UNAVAILABLE_DATASOURCE_RE = re.compile(r"\(datasource unavailable:\s*([^)]+)\)$")

from backend.app.agent.conversation import build_conversation_context
from backend.app.agent.nodes import build_context_node, generate_sql_node
from backend.app.agent.olap_intent import build_olap_hint, detect_olap_intents
from backend.app.agent.performance import explain_performance_node
from backend.app.agent.repair import iter_sql_repair_events
from backend.app.agent.state import AgentState
from backend.app.config import get_settings
from backend.app.config import retrieval_recovery_enabled
from backend.app.connectors.clickhouse import ClickHouseConnector
from backend.app.connectors.registry import get_datasource_manager
from backend.app.core.deepseek_provider import DeepSeekProvider
from backend.app.core.llm_provider import (
    LLMProvider,
    MockLLMProvider,
    SQLGenerationRequest,
    SQLGenerationResult,
)
from backend.app.execution.runner import QueryResult, execute_guarded_sql
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.metadata.retrieval_coverage import score_coverage
from backend.app.metadata.service import build_focused_context_from_retrieval, build_schema_context
from backend.app.sql_guard import build_default_guard_scope, guard_sql
from backend.app.visualization.recommender import recommend_chart


@dataclass
class RetrievalCheck:
    label: str
    expected: list[str]
    actual: list[str]
    missing: list[str]


@dataclass(frozen=True)
class DatasourceRef:
    name: str
    dialect: str
    display_name: str


@dataclass(frozen=True)
class CaseResources:
    datasource: DatasourceRef
    scope: Any
    full_schema_context: str


@dataclass(frozen=True)
class ProjectedQueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int


ResultLike = QueryResult | ProjectedQueryResult


@dataclass
class SmokeResult:
    case_id: str
    case_type: str
    question: str
    tags: list[str] = field(default_factory=list)
    datasource_name: str = DEFAULT_DATASOURCE
    datasource_dialect: str = "duckdb"
    datasource_display_name: str = "DuckDB (本地)"
    passed: bool = True
    messages: list[str] = field(default_factory=list)
    sql: str | None = None
    guard_stage: str | None = None
    row_count: int | None = None
    retrieval_fallback_used: bool | None = None
    retrieval_reference_coverage: dict[str, Any] | None = None
    retrieval_pre_coverage: dict[str, Any] | None = None
    retrieval_coverage: dict[str, Any] | None = None
    retrieval_tables: list[str] = field(default_factory=list)
    retrieval_columns: list[str] = field(default_factory=list)
    retrieval_metrics: list[str] = field(default_factory=list)
    retrieval_verified_queries: list[str] = field(default_factory=list)
    retrieval_vector_used: bool | None = None
    retrieval_index_status: str | None = None
    retrieval_stale_reason: str | None = None
    retrieval_value_hits: list[str] = field(default_factory=list)
    retrieval_checks: list[RetrievalCheck] = field(default_factory=list)
    focused_context_chars: int | None = None
    flags_off_context_chars: int | None = None
    flags_on_context_delta: int | None = None
    full_context_chars: int | None = None
    context_reduction_ratio: float | None = None
    error_category: str | None = None
    generated_sql: str | None = None
    normalized_sql: str | None = None
    chart_type: str | None = None
    guard_reason: str | None = None
    elapsed_ms: int | None = None
    repair_count: int = 0
    olap_intents: list[str] = field(default_factory=list)
    plan_hints: list[str] = field(default_factory=list)
    runtime_stats: dict[str, Any] | None = None
    expected_olap_intents: list[str] = field(default_factory=list)
    expected_sql_patterns: list[str] = field(default_factory=list)
    olap_intent_match: bool | None = None
    sql_pattern_match: bool | None = None
    chart_match: bool | None = None
    plan_hint_match: bool | None = None
    reference_result_match: bool | None = None
    is_follow_up: bool | None = None
    change_kind: str | None = None
    active_filters: list[str] = field(default_factory=list)

    def fail(self, message: str, error_category: str | None = None) -> None:
        self.passed = False
        self.messages.append(message)
        if error_category and self.error_category is None:
            self.error_category = error_category


class ScriptedRepairProvider:
    name = "mock"

    def __init__(self, *, initial_sql: str, repair_sqls: list[str] | None = None):
        self._initial_sql = initial_sql
        self._repair_sqls = list(repair_sqls or [])

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        if request.repair is None:
            return SQLGenerationResult(sql=self._initial_sql, provider=self.name)
        if self._repair_sqls:
            return SQLGenerationResult(sql=self._repair_sqls.pop(0), provider=self.name)
        return SQLGenerationResult(sql=self._initial_sql, provider=self.name)


class ScriptedConversationProvider:
    name = "mock"

    def __init__(self, turns: list[dict[str, Any]]):
        self._turns = turns
        self._turn_index = -1
        self._repair_sqls: list[str] = []

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        if request.repair is None:
            self._turn_index += 1
            turn = self._turns[self._turn_index]
            self._repair_sqls = list(turn.get("mock_repair_sqls") or [])
            return SQLGenerationResult(
                sql=turn["mock_sql"],
                provider=self.name,
                is_follow_up=bool(turn.get("is_follow_up", False)),
                change_kind=turn.get("change_kind", "none"),
            )
        if self._repair_sqls:
            return SQLGenerationResult(
                sql=self._repair_sqls.pop(0),
                provider=self.name,
                is_follow_up=True,
                change_kind="filter",
            )
        return SQLGenerationResult(
            sql=self._turns[self._turn_index]["mock_sql"],
            provider=self.name,
            is_follow_up=bool(self._turns[self._turn_index].get("is_follow_up", False)),
            change_kind=self._turns[self._turn_index].get("change_kind", "none"),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic smoke eval cases.")
    parser.add_argument(
        "cases_path",
        nargs="?",
        default="evals/smoke_cases.yaml",
        help="Path to smoke case YAML file.",
    )
    parser.add_argument(
        "--report-path",
        default="evals/reports/smoke_latest.md",
        help="Path for the Markdown smoke eval report.",
    )
    parser.add_argument(
        "--provider",
        choices=("mock", "deepseek"),
        default="mock",
        help="SQL generation provider to evaluate.",
    )
    parser.add_argument(
        "--vector-compare",
        action="store_true",
        help="Run each selected case in rule-only and rule+vector modes, then write a comparison report.",
    )
    parser.add_argument(
        "--require-clickhouse",
        action="store_true",
        help="Fail if any ClickHouse-listed case is skipped because ClickHouse is unavailable.",
    )
    parser.add_argument(
        "--retrieval-calibration",
        action="store_true",
        help="Sweep retrieval coverage thresholds with vector retrieval enabled and report recovery/regression tradeoffs.",
    )
    parser.add_argument(
        "--retrieval-thresholds",
        default="0.3,0.4,0.5,0.6,0.7,0.8",
        help="Comma-separated thresholds for --retrieval-calibration.",
    )
    args = parser.parse_args()

    try:
        cases = _load_cases(Path(args.cases_path))
        provider = _create_provider(args.provider)
        available_datasources = _available_datasources()
        selected_cases, skipped_case_ids = _filter_cases(
            cases,
            provider_name=args.provider,
            available_datasources=available_datasources,
            force_retrieval_recovery=args.retrieval_calibration,
        )
        if args.require_clickhouse and _has_unavailable_clickhouse_skip(skipped_case_ids):
            print(
                "error: ClickHouse is required, but at least one ClickHouse case was skipped.",
                file=sys.stderr,
            )
            for skipped in skipped_case_ids:
                if _is_unavailable_clickhouse_skip(skipped):
                    print(f"  - {skipped}", file=sys.stderr)
            return 1
        resources_by_datasource = _build_case_resources(selected_cases, available_datasources)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.vector_compare:
        return _run_vector_compare(
            selected_cases=selected_cases,
            provider=provider,
            provider_name=args.provider,
            report_path=Path(args.report_path),
            skipped_case_ids=skipped_case_ids,
            resources_by_datasource=resources_by_datasource,
        )
    if args.retrieval_calibration:
        return _run_retrieval_calibration(
            selected_cases=selected_cases,
            provider=provider,
            provider_name=args.provider,
            report_path=Path(args.report_path),
            skipped_case_ids=skipped_case_ids,
            resources_by_datasource=resources_by_datasource,
            thresholds=_parse_thresholds(args.retrieval_thresholds),
        )

    results = [
        _run_case(
            case,
            resources_by_datasource[_case_datasource(case)],
            provider,
            provider_name=args.provider,
            use_vector=False,
        )
        for case in selected_cases
    ]
    _validate_parity_anchor_results(results)
    report_path = Path(args.report_path)
    _write_report(report_path, results, provider_name=args.provider, skipped_case_ids=skipped_case_ids)
    _print_results(results, report_path, provider_name=args.provider, skipped_case_ids=skipped_case_ids)
    return 0 if all(result.passed for result in results) else 1


def _run_vector_compare(
    *,
    selected_cases: list[dict[str, Any]],
    provider: LLMProvider,
    provider_name: str,
    report_path: Path,
    skipped_case_ids: list[str],
    resources_by_datasource: dict[str, CaseResources],
) -> int:
    rule_results = [
        _run_case(
            case,
            resources_by_datasource[_case_datasource(case)],
            provider,
            provider_name=provider_name,
            use_vector=False,
        )
        for case in selected_cases
    ]
    _validate_parity_anchor_results(rule_results)
    vector_results = [
        _run_case(
            case,
            resources_by_datasource[_case_datasource(case)],
            provider,
            provider_name=provider_name,
            use_vector=True,
            validate_vector_expectations=True,
        )
        for case in selected_cases
    ]
    _validate_parity_anchor_results(vector_results)
    _write_vector_compare_report(
        report_path,
        rule_results=rule_results,
        vector_results=vector_results,
        provider_name=provider_name,
        skipped_case_ids=skipped_case_ids,
    )
    _print_vector_compare_results(rule_results, vector_results, report_path)
    return 0 if all(result.passed for result in [*rule_results, *vector_results]) else 1


def _run_retrieval_calibration(
    *,
    selected_cases: list[dict[str, Any]],
    provider: LLMProvider,
    provider_name: str,
    report_path: Path,
    skipped_case_ids: list[str],
    resources_by_datasource: dict[str, CaseResources],
    thresholds: list[float],
) -> int:
    reports = []
    all_results: list[SmokeResult] = []
    reference_threshold = float(get_settings().retrieval_coverage_threshold)
    for threshold in thresholds:
        with _temporary_retrieval_recovery(threshold):
            results = [
                _run_case(
                    case,
                    resources_by_datasource[_case_datasource(case)],
                    provider,
                    provider_name=provider_name,
                    use_vector=True,
                    validate_coverage_expectations=False,
                    reference_threshold=reference_threshold,
                )
                for case in selected_cases
            ]
        _validate_parity_anchor_results(results)
        all_results.extend(results)
        reports.append(_retrieval_calibration_row(threshold, results))

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _render_retrieval_calibration_report(
            reports,
            provider_name=provider_name,
            skipped_case_ids=skipped_case_ids,
            reference_threshold=reference_threshold,
        ),
        encoding="utf-8",
    )
    print(f"retrieval calibration thresholds: {', '.join(str(row['threshold']) for row in reports)}")
    print(f"reference holdout threshold: {reference_threshold}")
    for row in reports:
        print(
            "threshold="
            f"{row['threshold']}: recovery={row['recovery_passed']}/{row['recovery_cases']} "
            f"fallback_paths={row['fallback_path_passed']}/{row['fallback_path_cases']} "
            f"high_conf_regressions={row['high_conf_regressions']}/{row['high_conf_cases']}"
        )
    print(f"report: {report_path}")
    return 0 if all(result.passed for result in all_results) else 1


@contextmanager
def _temporary_retrieval_recovery(threshold: float) -> Iterator[None]:
    settings = get_settings()
    original = {
        "retrieval_expansion_enabled": settings.retrieval_expansion_enabled,
        "retrieval_fallback_mode": settings.retrieval_fallback_mode,
        "retrieval_coverage_threshold": settings.retrieval_coverage_threshold,
    }
    settings.retrieval_expansion_enabled = True
    settings.retrieval_fallback_mode = "on"
    settings.retrieval_coverage_threshold = threshold
    try:
        yield
    finally:
        for name, value in original.items():
            setattr(settings, name, value)


@contextmanager
def _temporary_retrieval_threshold(threshold: float) -> Iterator[None]:
    settings = get_settings()
    original = settings.retrieval_coverage_threshold
    settings.retrieval_coverage_threshold = threshold
    try:
        yield
    finally:
        settings.retrieval_coverage_threshold = original


def _parse_thresholds(raw_value: str) -> list[float]:
    thresholds = []
    for item in raw_value.split(","):
        value = item.strip()
        if not value:
            continue
        threshold = float(value)
        if threshold < 0.0 or threshold > 1.0:
            raise ValueError("retrieval thresholds must be between 0 and 1.")
        thresholds.append(threshold)
    if not thresholds:
        raise ValueError("at least one retrieval threshold is required.")
    return thresholds


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("smoke case file must contain a list under 'cases'.")
    return cases


def _create_provider(provider_name: str) -> LLMProvider:
    if provider_name == "mock":
        return MockLLMProvider()
    if provider_name == "deepseek":
        if not get_settings().deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for real LLM eval.")
        return DeepSeekProvider()
    raise ValueError(f"Unknown provider: {provider_name}")


def _filter_cases(
    cases: list[dict[str, Any]],
    provider_name: str,
    available_datasources: dict[str, DatasourceRef],
    *,
    force_retrieval_recovery: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    selected_cases = []
    skipped_case_ids = []
    for case in cases:
        case_provider = case.get("provider", "both")
        if case_provider not in {"both", "mock", "real"}:
            raise ValueError(f"Unknown case provider {case_provider!r} in {case['id']}.")
        if not _case_matches_provider(case_provider, provider_name):
            skipped_case_ids.append(f"{case['id']} (provider={case_provider})")
            continue
        if (
            case.get("requires_retrieval_recovery")
            and not force_retrieval_recovery
            and not retrieval_recovery_enabled()
        ):
            skipped_case_ids.append(f"{case['id']} (requires retrieval recovery)")
            continue
        for datasource_name in _case_datasources(case):
            if datasource_name not in available_datasources:
                skipped_case_ids.append(f"{case['id']} (datasource unavailable: {datasource_name})")
                continue
            selected_case = dict(case)
            selected_case["_selected_datasource"] = datasource_name
            selected_cases.append(selected_case)
    return selected_cases, skipped_case_ids


def _case_matches_provider(case_provider: str, provider_name: str) -> bool:
    if case_provider == "both":
        return True
    if case_provider == "mock":
        return provider_name == "mock"
    return provider_name != "mock"


def _case_datasource(case: dict[str, Any]) -> str:
    return str(case.get("_selected_datasource") or _case_datasources(case)[0])


def _case_datasources(case: dict[str, Any]) -> list[str]:
    if "datasource" in case and "datasources" in case:
        raise ValueError(f"Case {case['id']} cannot set both 'datasource' and 'datasources'.")
    if "datasource" in case:
        datasource_name = str(case.get("datasource") or "").strip()
        if not datasource_name:
            raise ValueError(f"Case {case['id']} has a blank datasource.")
        return [datasource_name]
    if "datasources" in case:
        datasources = case.get("datasources")
        if not isinstance(datasources, list) or not datasources:
            raise ValueError(f"Case {case['id']} datasources must be a non-empty list.")
        normalized = [str(datasource).strip() for datasource in datasources]
        if any(not datasource for datasource in normalized):
            raise ValueError(f"Case {case['id']} datasources cannot contain blanks.")
        return normalized
    return [DEFAULT_DATASOURCE]


def _has_unavailable_clickhouse_skip(skipped_case_ids: list[str]) -> bool:
    return any(_is_unavailable_clickhouse_skip(skipped) for skipped in skipped_case_ids)


def _is_unavailable_clickhouse_skip(skipped: str) -> bool:
    return _unavailable_datasource_name(skipped) == ClickHouseConnector.name


def _unavailable_datasource_name(skipped: str) -> str | None:
    match = UNAVAILABLE_DATASOURCE_RE.search(skipped)
    if match is None:
        return None
    return match.group(1).strip()


def _available_datasources() -> dict[str, DatasourceRef]:
    manager = get_datasource_manager()
    return {
        source.name: DatasourceRef(
            name=source.name,
            dialect=source.dialect,
            display_name=source.display_name,
        )
        for source in manager.list_sources()
    }


def _build_case_resources(
    cases: list[dict[str, Any]],
    available_datasources: dict[str, DatasourceRef],
) -> dict[str, CaseResources]:
    resources: dict[str, CaseResources] = {}
    for datasource_name in sorted({_case_datasource(case) for case in cases}):
        datasource = available_datasources[datasource_name]
        resources[datasource_name] = CaseResources(
            datasource=datasource,
            scope=build_default_guard_scope(datasource_name=datasource_name),
            full_schema_context=build_schema_context(datasource_name=datasource_name),
        )
    return resources


def _run_case(
    case: dict[str, Any],
    resources: CaseResources,
    provider: LLMProvider,
    provider_name: str,
    use_vector: bool | None = None,
    validate_vector_expectations: bool = False,
    validate_coverage_expectations: bool = True,
    reference_threshold: float | None = None,
) -> SmokeResult:
    if case.get("type") == "conversation":
        return _run_conversation_case(
            case,
            resources,
            provider_name=provider_name,
            use_vector=use_vector,
        )

    datasource = resources.datasource
    result = SmokeResult(
        case_id=case["id"],
        case_type=case.get("type", ""),
        question=case["question"],
        tags=list(case.get("tags") or []),
        datasource_name=datasource.name,
        datasource_dialect=datasource.dialect,
        datasource_display_name=datasource.display_name,
    )
    started_at = time.perf_counter()
    try:
        expected = case.get("expected", {})
        try:
            retrieval_result = _case_retrieval_result(
                case,
                use_vector=use_vector,
                datasource_name=datasource.name,
            )
            _record_retrieval_result(result, retrieval_result)
            _validate_retrieval(result, retrieval_result, expected.get("retrieval") or {})
            if validate_vector_expectations:
                _validate_vector_retrieval(result, expected.get("vector") or {})
            olap_intents = [str(intent) for intent in detect_olap_intents(case["question"])]
            if reference_threshold is not None:
                with _temporary_retrieval_threshold(reference_threshold):
                    reference_coverage = score_coverage(
                        {**copy.deepcopy(retrieval_result), "retrieval_stage": "merged"},
                        datasource_name=datasource.name,
                        olap_intents=olap_intents,
                    )
                result.retrieval_reference_coverage = reference_coverage.as_dict()
            pre_coverage = score_coverage(
                {**copy.deepcopy(retrieval_result), "retrieval_stage": "merged"},
                datasource_name=datasource.name,
                olap_intents=olap_intents,
            )
            result.retrieval_pre_coverage = pre_coverage.as_dict()
            flags_off_context = build_focused_context_from_retrieval(
                copy.deepcopy(retrieval_result),
                datasource_name=datasource.name,
            )
        except Exception as exc:
            result.fail(f"retrieval/context failed: {exc}", "retrieval_miss")
            return result

        result.flags_off_context_chars = len(flags_off_context)
        result.full_context_chars = len(resources.full_schema_context)

        state = AgentState(
            question=case["question"],
            datasource_name=datasource.name,
            datasource_dialect=datasource.dialect,
            datasource_display_name=datasource.display_name,
            retrieval_result=retrieval_result,
            olap_intents=olap_intents,
        )
        try:
            build_context_node(state)
        except Exception as exc:
            result.fail(f"context recovery failed: {exc}", "retrieval_miss")
            return result
        result.focused_context_chars = len(state.schema_context or "")
        result.flags_on_context_delta = (
            result.focused_context_chars - result.flags_off_context_chars
            if result.flags_off_context_chars is not None
            else None
        )
        result.full_context_chars = len(resources.full_schema_context)
        result.context_reduction_ratio = _context_reduction_ratio(
            focused_chars=result.focused_context_chars,
            full_chars=result.full_context_chars,
        )
        _record_state_result(result, state)
        if validate_coverage_expectations:
            _validate_coverage_expectations(result, expected.get("coverage") or {})
        _set_olap_hint(state)
        case_provider = _case_provider(case, provider=provider, provider_name=provider_name)
        try:
            generate_sql_node(state, provider=case_provider)
        except httpx.ReadTimeout as exc:
            result.fail(f"SQL generation timed out: {exc}", "sql_generation_timeout")
            return result
        except Exception as exc:
            result.fail(f"SQL generation failed: {exc}", "sql_generation_error")
            return result

        result.sql = state.sql
        result.generated_sql = state.sql
        if not (state.sql or "").strip():
            result.fail("SQL generation returned empty SQL.", "sql_generation_error")
            return result

        if expected.get("should_execute") is False and not expected.get("repair_should_fail"):
            _run_guard_only_case(
                result=result,
                state=state,
                scope=resources.scope,
                expected=expected,
                datasource_name=datasource.name,
            )
            return result

        try:
            repair_events = list(
                iter_sql_repair_events(
                    state,
                    provider=case_provider,
                    scope_builder=lambda datasource_name=datasource.name: resources.scope,
                    executor=execute_guarded_sql,
                )
            )
        except Exception as exc:
            result.fail(f"SQL repair workflow failed: {exc}", "repair_error")
            return result

        result.repair_count = len(state.repair_history)
        _validate_repair_expectations(result, expected)

        final_event = repair_events[-1] if repair_events else None
        if expected.get("repair_should_fail"):
            _record_state_result(result, state)
            _validate_expected_repair_failure(result, final_event, expected)
            return result

        if final_event and final_event.step == "error":
            _record_state_result(result, state)
            error_category = (
                _guard_error_category(final_event.error_kind)
                if final_event.error_stage == "sql_guard"
                else "execution_error"
            )
            result.fail(
                f"expected execution, but repair workflow failed: {final_event.error_reason}",
                error_category,
            )
            return result

        if state.query_result is None:
            result.fail("repair workflow finished without query result.", "execution_error")
            return result

        explain_performance_node(state)
        _record_state_result(result, state)

        reference_result_match = _validate_reference_result(
            result=result,
            reference_sql=case.get("mock_sql"),
            query_result=state.query_result,
            scope=resources.scope,
            datasource_name=datasource.name,
            provider_name=provider_name,
        )

        chart_recommendation = recommend_chart(state.query_result, olap_intents=state.olap_intents)
        result.chart_type = chart_recommendation.chart_type

        _validate_normal_case(
            result=result,
            expected=expected,
            matched_query_id=state.matched_query_id,
            query_result=state.query_result,
            explainability=state.explainability or {},
            chart_type=chart_recommendation.chart_type,
            provider_name=provider_name,
            reference_result_match=reference_result_match,
        )
        return result
    finally:
        result.elapsed_ms = round((time.perf_counter() - started_at) * 1000)


def _set_olap_hint(state: AgentState) -> None:
    metrics = []
    if state.retrieval_result is not None:
        metrics = state.retrieval_result.get("metrics", [])
    if not state.olap_intents:
        state.olap_intents = list(detect_olap_intents(state.question))
    state.olap_hint = build_olap_hint(
        state.olap_intents,
        datasource_dialect=state.datasource_dialect,
        matched_metrics=metrics,
    )
    state.completed_steps.append("olap_detected")


def _run_conversation_case(
    case: dict[str, Any],
    resources: CaseResources,
    *,
    provider_name: str,
    use_vector: bool | None = None,
) -> SmokeResult:
    datasource = resources.datasource
    turns = list(case.get("turns") or [])
    result = SmokeResult(
        case_id=case["id"],
        case_type=case.get("type", ""),
        question=" -> ".join(turn.get("question", "") for turn in turns),
        tags=list(case.get("tags") or []),
        datasource_name=datasource.name,
        datasource_dialect=datasource.dialect,
        datasource_display_name=datasource.display_name,
    )
    started_at = time.perf_counter()
    provider = ScriptedConversationProvider(turns)
    conversation_context = None
    final_state = None
    try:
        for turn in turns:
            state = AgentState(
                question=turn["question"],
                datasource_name=datasource.name,
                datasource_dialect=datasource.dialect,
                datasource_display_name=datasource.display_name,
                conversation_context=conversation_context,
            )
            try:
                state.retrieval_result = retrieve_metadata_assets(
                    turn["question"],
                    use_vector=use_vector,
                    datasource_name=datasource.name,
                )
                build_context_node(state)
                result.focused_context_chars = len(state.schema_context or "")
                result.full_context_chars = len(resources.full_schema_context)
                result.context_reduction_ratio = _context_reduction_ratio(
                    focused_chars=result.focused_context_chars,
                    full_chars=result.full_context_chars,
                )
            except Exception as exc:
                result.fail(f"conversation retrieval/context failed: {exc}", "retrieval_miss")
                return result

            _set_olap_hint(state)
            try:
                generate_sql_node(state, provider=provider)
            except Exception as exc:
                result.fail(f"conversation SQL generation failed: {exc}", "sql_generation_error")
                return result

            try:
                repair_events = list(
                    iter_sql_repair_events(
                        state,
                        provider=provider,
                        scope_builder=lambda datasource_name=datasource.name: resources.scope,
                        executor=execute_guarded_sql,
                    )
                )
            except Exception as exc:
                result.fail(f"conversation repair workflow failed: {exc}", "repair_error")
                return result

            final_event = repair_events[-1] if repair_events else None
            if final_event and final_event.step == "error":
                _record_state_result(result, state)
                result.fail(
                    f"conversation turn failed: {final_event.error_reason}",
                    "execution_error",
                )
                return result
            if state.query_result is None:
                result.fail("conversation turn finished without query result.", "execution_error")
                return result

            explain_performance_node(state)
            conversation_context = build_conversation_context(state)
            final_state = state

        if final_state is None:
            result.fail("conversation case has no turns.", "sql_generation_error")
            return result

        _record_state_result(result, final_state)
        result.repair_count = len(final_state.repair_history)
        if conversation_context is not None:
            result.active_filters = [predicate.label() for predicate in conversation_context.active_filters]

        query_result = final_state.query_result
        if query_result is None:
            result.fail("conversation turn finished without query result.", "execution_error")
            return result
        chart_recommendation = recommend_chart(query_result, olap_intents=final_state.olap_intents)
        result.chart_type = chart_recommendation.chart_type
        expected = case.get("expected", {})
        _validate_normal_case(
            result=result,
            expected=expected,
            matched_query_id=final_state.matched_query_id,
            query_result=final_state.query_result,
            explainability=final_state.explainability or {},
            chart_type=chart_recommendation.chart_type,
            provider_name=provider_name,
        )
        _validate_conversation_expectations(result, expected)
        return result
    finally:
        result.elapsed_ms = round((time.perf_counter() - started_at) * 1000)


def _record_retrieval_result(result: SmokeResult, retrieval_result: dict[str, Any]) -> None:
    retrieval_meta = retrieval_result.get("retrieval_meta") or {}
    result.retrieval_fallback_used = bool(retrieval_result.get("fallback_used"))
    result.retrieval_coverage = retrieval_result.get("retrieval_coverage")
    result.retrieval_tables = [
        table["table_name"] for table in retrieval_result.get("tables") or []
    ]
    result.retrieval_columns = [
        f"{column['table_name']}.{column['column_name']}"
        for column in retrieval_result.get("columns") or []
    ]
    result.retrieval_metrics = [
        metric["name"] for metric in retrieval_result.get("metrics") or []
    ]
    result.retrieval_verified_queries = [
        query["id"] for query in retrieval_result.get("verified_queries") or []
    ]
    result.retrieval_vector_used = retrieval_meta.get("vector_used")
    result.retrieval_index_status = retrieval_meta.get("index_status")
    result.retrieval_stale_reason = retrieval_meta.get("stale_reason")
    result.retrieval_value_hits = [
        _format_value_hit(hit) for hit in retrieval_meta.get("value_hits") or []
    ]


def _context_reduction_ratio(focused_chars: int, full_chars: int) -> float | None:
    if full_chars <= 0:
        return None
    return 1 - focused_chars / full_chars


def _case_provider(
    case: dict[str, Any],
    *,
    provider: LLMProvider,
    provider_name: str,
) -> LLMProvider:
    if provider_name == "mock" and case.get("mock_sql"):
        return ScriptedRepairProvider(
            initial_sql=case["mock_sql"],
            repair_sqls=case.get("mock_repair_sqls") or [],
        )
    return provider


def _case_retrieval_result(
    case: dict[str, Any],
    *,
    use_vector: bool | None,
    datasource_name: str,
) -> dict[str, Any]:
    fixture = case.get("retrieval_fixture")
    if fixture is not None:
        if not isinstance(fixture, dict):
            raise ValueError(f"Case {case['id']} retrieval_fixture must be a mapping.")
        result = copy.deepcopy(fixture)
        result.setdefault("question", case["question"])
        result.setdefault("datasource", datasource_name)
        result.setdefault("tables", [])
        result.setdefault("columns", [])
        result.setdefault("metrics", [])
        result.setdefault("verified_queries", [])
        result.setdefault("retrieval_meta", {})
        return result
    return retrieve_metadata_assets(
        case["question"],
        use_vector=use_vector,
        datasource_name=datasource_name,
    )


def _run_guard_only_case(
    *,
    result: SmokeResult,
    state: AgentState,
    scope,
    expected: dict[str, Any],
    datasource_name: str,
) -> None:
    try:
        guard_result = guard_sql(state.sql or "", scope=scope, datasource_name=datasource_name)
    except Exception as exc:
        result.fail(f"SQL Guard failed: {exc}", "guard_blocked")
        return

    state.guard_result = guard_result
    _record_state_result(result, state)
    result.guard_stage = guard_result.stage
    result.guard_reason = guard_result.reason
    result.normalized_sql = guard_result.normalized_sql
    _validate_dialect_hints(result, expected)
    _validate_safety_case(result, guard_result, expected)
    _validate_repair_expectations(result, expected)


def _record_state_result(result: SmokeResult, state: AgentState) -> None:
    result.sql = state.sql
    result.generated_sql = state.sql
    result.repair_count = len(state.repair_history)
    result.olap_intents = list(state.olap_intents)
    result.plan_hints = list(state.plan_hints)
    result.runtime_stats = state.runtime_stats
    result.retrieval_coverage = state.retrieval_coverage or result.retrieval_coverage
    if result.retrieval_coverage is not None:
        result.retrieval_fallback_used = bool(result.retrieval_coverage.get("fallback_used"))
    result.is_follow_up = state.is_follow_up
    result.change_kind = state.change_kind
    if state.guard_result is not None:
        result.guard_stage = state.guard_result.stage
        result.guard_reason = state.guard_result.reason
        result.normalized_sql = state.guard_result.normalized_sql
    if state.query_result is not None:
        result.row_count = state.query_result.row_count


def _validate_conversation_expectations(result: SmokeResult, expected: dict[str, Any]) -> None:
    expected_follow_up = expected.get("is_follow_up")
    if expected_follow_up is not None and result.is_follow_up != expected_follow_up:
        result.fail(
            f"expected is_follow_up {expected_follow_up}, got {result.is_follow_up}",
            "conversation_mismatch",
        )

    expected_change_kind = expected.get("change_kind")
    if expected_change_kind and result.change_kind != expected_change_kind:
        result.fail(
            f"expected change_kind {expected_change_kind}, got {result.change_kind}",
            "conversation_mismatch",
        )

    for expected_filter in expected.get("active_filters") or []:
        if expected_filter not in result.active_filters:
            result.fail(
                f"expected active filter {expected_filter!r}, got {result.active_filters}",
                "conversation_mismatch",
            )

    normalized_sql = result.normalized_sql or result.generated_sql or ""
    for keyword in expected.get("normalized_sql_contains") or []:
        if keyword not in normalized_sql:
            result.fail(
                f"expected normalized SQL to contain {keyword!r}, got {normalized_sql!r}",
                "conversation_mismatch",
            )


def _validate_repair_expectations(result: SmokeResult, expected: dict[str, Any]) -> None:
    expected_count = expected.get("repair_count")
    if expected_count is not None and result.repair_count != expected_count:
        result.fail(
            f"expected repair_count {expected_count}, got {result.repair_count}",
            "repair_mismatch",
        )


def _validate_expected_repair_failure(
    result: SmokeResult,
    final_event,
    expected: dict[str, Any],
) -> None:
    if final_event is None or final_event.step != "error":
        result.fail("expected repair workflow failure, but query succeeded.", "repair_mismatch")
        return

    expected_stage = expected.get("guard_stage")
    if expected_stage and final_event.error_kind != expected_stage:
        result.fail(
            f"expected final guard stage {expected_stage}, got {final_event.error_kind}",
            "repair_mismatch",
        )

    reason_contains = expected.get("reason_contains")
    if reason_contains and reason_contains not in (final_event.error_reason or ""):
        result.fail(
            f"expected final reason to contain {reason_contains!r}, "
            f"got {final_event.error_reason!r}",
            "repair_mismatch",
        )


def _validate_retrieval(
    result: SmokeResult,
    retrieval_result: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    if not expected:
        return

    expected_fallback = expected.get("fallback_used")
    if expected_fallback is not None and retrieval_result.get("fallback_used") != expected_fallback:
        result.fail(
            f"expected retrieval fallback_used {expected_fallback}, "
            f"got {retrieval_result.get('fallback_used')}",
            "retrieval_miss",
        )

    _validate_retrieval_subset(
        result,
        label="retrieval tables",
        expected=expected.get("required_tables") or [],
        actual=result.retrieval_tables,
    )
    _validate_retrieval_subset(
        result,
        label="retrieval columns",
        expected=expected.get("required_columns") or [],
        actual=result.retrieval_columns,
    )
    _validate_retrieval_subset(
        result,
        label="retrieval metrics",
        expected=expected.get("required_metrics") or [],
        actual=result.retrieval_metrics,
    )
    _validate_retrieval_subset(
        result,
        label="retrieval verified queries",
        expected=expected.get("required_verified_queries") or [],
        actual=result.retrieval_verified_queries,
    )


def _validate_coverage_expectations(result: SmokeResult, expected: dict[str, Any]) -> None:
    if not expected:
        return

    _validate_coverage_value(
        result,
        label="pre_band",
        expected=expected.get("pre_band"),
        actual=(result.retrieval_pre_coverage or {}).get("band"),
    )
    _validate_coverage_value(
        result,
        label="post_band",
        expected=expected.get("post_band"),
        actual=(result.retrieval_coverage or {}).get("band"),
    )
    _validate_coverage_value(
        result,
        label="expanded",
        expected=expected.get("expanded"),
        actual=(result.retrieval_coverage or {}).get("expanded"),
    )
    _validate_coverage_value(
        result,
        label="fallback_used",
        expected=expected.get("fallback_used"),
        actual=(result.retrieval_coverage or {}).get("fallback_used"),
    )


def _validate_coverage_value(
    result: SmokeResult,
    *,
    label: str,
    expected: Any,
    actual: Any,
) -> None:
    if expected is None:
        return
    if actual != expected:
        result.fail(
            f"expected coverage {label} {expected!r}, got {actual!r}",
            "retrieval_coverage_mismatch",
        )


def _validate_parity_anchor_results(results: list[SmokeResult]) -> None:
    by_case_id: dict[str, list[SmokeResult]] = {}
    for result in results:
        by_case_id.setdefault(result.case_id, []).append(result)

    for case_id, group in by_case_id.items():
        if len(group) <= 1:
            continue
        bands = {
            result.datasource_name: (result.retrieval_coverage or {}).get("band")
            for result in group
        }
        missing_band_datasources = [
            datasource_name for datasource_name, band in bands.items() if band is None
        ]
        if len(missing_band_datasources) == len(bands):
            continue
        if missing_band_datasources:
            for result in group:
                result.fail(
                    f"parity anchor missing coverage band for {case_id}: {missing_band_datasources}",
                    "retrieval_coverage_mismatch",
                )
            continue
        unique_bands = set(bands.values())
        if len(unique_bands) <= 1:
            continue
        for result in group:
            result.fail(
                f"parity anchor coverage band mismatch for {case_id}: {bands}",
                "retrieval_coverage_mismatch",
            )


def _validate_vector_retrieval(
    result: SmokeResult,
    expected: dict[str, Any],
) -> None:
    if not expected:
        return

    expected_vector_used = expected.get("vector_used")
    if expected_vector_used is not None and result.retrieval_vector_used != expected_vector_used:
        result.fail(
            f"expected vector_used {expected_vector_used}, got {result.retrieval_vector_used}",
            "retrieval_miss",
        )

    expected_index_status = expected.get("index_status")
    if expected_index_status and result.retrieval_index_status != expected_index_status:
        result.fail(
            f"expected index_status {expected_index_status}, got {result.retrieval_index_status}",
            "retrieval_miss",
        )

    _validate_retrieval_subset(
        result,
        label="vector retrieval tables",
        expected=expected.get("required_tables") or [],
        actual=result.retrieval_tables,
    )
    _validate_retrieval_subset(
        result,
        label="vector retrieval columns",
        expected=expected.get("required_columns") or [],
        actual=result.retrieval_columns,
    )
    _validate_retrieval_subset(
        result,
        label="vector retrieval metrics",
        expected=expected.get("required_metrics") or [],
        actual=result.retrieval_metrics,
    )
    _validate_retrieval_subset(
        result,
        label="retrieval value hits",
        expected=expected.get("required_value_hits") or [],
        actual=result.retrieval_value_hits,
    )


def _validate_safety_case(result: SmokeResult, guard_result, expected: dict[str, Any]) -> None:
    if guard_result.allowed:
        result.fail("expected Guard rejection, but SQL was allowed", "guard_mismatch")
        return

    expected_stage = expected.get("guard_stage")
    if expected_stage and guard_result.stage != expected_stage:
        result.fail(
            f"expected Guard stage {expected_stage}, got {guard_result.stage}",
            "guard_mismatch",
        )

    reason_contains = expected.get("reason_contains")
    if reason_contains and reason_contains not in (guard_result.reason or ""):
        result.fail(
            f"expected reason to contain {reason_contains!r}, got {guard_result.reason!r}",
            "guard_mismatch",
        )


def _validate_normal_case(
    result: SmokeResult,
    expected: dict[str, Any],
    matched_query_id: str | None,
    query_result,
    explainability: dict[str, Any],
    chart_type: str,
    provider_name: str,
    reference_result_match: bool | None = None,
) -> None:
    if provider_name == "mock" and matched_query_id != expected.get("matched_query_id"):
        result.fail(
            f"expected matched_query_id {expected.get('matched_query_id')}, got {matched_query_id}",
            "result_mismatch",
        )

    _validate_dialect_hints(result, expected)
    _validate_olap_expectations(result, expected)
    _validate_required_sql_patterns(result, expected)

    expected_sql_keywords = expected.get("expected_sql_keywords") or []
    if expected_sql_keywords and result.generated_sql:
        normalized_sql = result.generated_sql.lower()
        missing_keywords = [
            keyword for keyword in expected_sql_keywords if keyword.lower() not in normalized_sql
        ]
        if missing_keywords:
            result.fail(
                f"missing expected SQL keywords: {missing_keywords}",
                "sql_generation_mismatch",
            )

    expected_columns = expected.get("result_columns") or []
    if reference_result_match is not True and query_result.columns != expected_columns:
        result.fail(
            f"expected result columns {expected_columns}, got {query_result.columns}",
            "result_mismatch",
        )

    min_row_count = expected.get("min_row_count")
    if min_row_count is not None and query_result.row_count < min_row_count:
        result.fail(
            f"expected at least {min_row_count} rows, got {query_result.row_count}",
            "result_mismatch",
        )

    expected_chart_type = expected.get("chart_type")
    if expected_chart_type and chart_type != expected_chart_type:
        result.fail(
            f"expected chart type {expected_chart_type}, got {chart_type}",
            "chart_mismatch",
        )
    if "phase65" in result.tags and expected_chart_type:
        result.chart_match = chart_type == expected_chart_type

    _validate_plan_hint_expectation(result, expected)

    if reference_result_match is not True:
        _validate_subset(
            result,
            label="tables",
            expected=expected.get("required_tables") or [],
            actual=explainability.get("matched_tables") or [],
            error_category="explainability_mismatch",
        )
        _validate_subset(
            result,
            label="columns",
            expected=expected.get("required_columns") or [],
            actual=explainability.get("matched_columns") or [],
            error_category="explainability_mismatch",
        )
        _validate_subset(
            result,
            label="join paths",
            expected=expected.get("join_paths") or [],
            actual=[_format_join_path(path) for path in explainability.get("join_paths") or []],
            error_category="explainability_mismatch",
        )


def _validate_reference_result(
    *,
    result: SmokeResult,
    reference_sql: str | None,
    query_result,
    scope,
    datasource_name: str,
    provider_name: str,
) -> bool | None:
    if provider_name == "mock" or not reference_sql:
        return None

    try:
        guard_result = guard_sql(reference_sql, scope=scope, datasource_name=datasource_name)
        if not guard_result.allowed:
            result.fail(
                f"reference SQL rejected by {guard_result.stage}: {guard_result.reason}",
                "result_mismatch",
            )
            result.reference_result_match = False
            return False
        reference_result = execute_guarded_sql(guard_result, datasource_name=datasource_name)
    except Exception as exc:
        result.fail(f"reference SQL execution failed: {exc}", "result_mismatch")
        result.reference_result_match = False
        return False

    comparison = _compare_query_results(query_result, reference_result)
    result.reference_result_match = comparison["match"]
    if not comparison["match"]:
        result.fail(comparison["reason"], "result_mismatch")
    return comparison["match"]


def _compare_query_results(actual: QueryResult, expected: QueryResult) -> dict[str, Any]:
    aligned_actual, aligned_expected = _align_named_column_subset(actual, expected)
    if len(aligned_actual.columns) != len(aligned_expected.columns):
        return {
            "match": False,
            "reason": (
                f"reference result column count {len(aligned_expected.columns)} "
                f"!= actual {len(aligned_actual.columns)}"
            ),
        }
    if aligned_actual.row_count != aligned_expected.row_count:
        return {
            "match": False,
            "reason": (
                f"reference result row_count {aligned_expected.row_count} "
                f"!= actual {aligned_actual.row_count}"
            ),
        }

    actual_rows = sorted((_normalize_row(row) for row in aligned_actual.rows), key=repr)
    expected_rows = sorted((_normalize_row(row) for row in aligned_expected.rows), key=repr)
    for index, (actual_row, expected_row) in enumerate(zip(actual_rows, expected_rows, strict=True)):
        if not _rows_equal(actual_row, expected_row):
            return {
                "match": False,
                "reason": f"reference result row {index} differs: expected={expected_row}, actual={actual_row}",
            }
    return {"match": True, "reason": ""}


def _align_named_column_subset(actual: QueryResult, expected: QueryResult) -> tuple[ResultLike, ResultLike]:
    if len(actual.columns) == len(expected.columns):
        return actual, expected
    if len(actual.columns) > len(expected.columns):
        return _project_named_columns(actual, expected.columns), expected
    missing_expected_columns = [
        column for column in expected.columns if column not in actual.columns
    ]
    if (
        all(column in expected.columns for column in actual.columns)
        and all(_is_optional_identifier_column(column) for column in missing_expected_columns)
    ):
        return actual, _project_named_columns(expected, actual.columns)
    return actual, expected


def _is_optional_identifier_column(column: str) -> bool:
    normalized = column.lower()
    return normalized.endswith("_id") or normalized.endswith("_key")


def _project_named_columns(query_result: QueryResult, columns: list[str]) -> ResultLike:
    if not all(column in query_result.columns for column in columns):
        return query_result

    indexes = [query_result.columns.index(column) for column in columns]
    return ProjectedQueryResult(
        columns=list(columns),
        rows=[[row[index] for index in indexes] for row in query_result.rows],
        row_count=query_result.row_count,
    )


def _normalize_row(row: list[Any]) -> tuple[Any, ...]:
    return tuple(_normalize_value(value) for value in row)


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return _decimal_value(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("NaN")


def _rows_equal(actual_row: tuple[Any, ...], expected_row: tuple[Any, ...]) -> bool:
    if len(actual_row) != len(expected_row):
        return False
    return all(
        _values_equal(actual, expected)
        for actual, expected in zip(actual_row, expected_row, strict=True)
    )


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, Decimal) and isinstance(expected, Decimal):
        if actual.is_nan() or expected.is_nan():
            return False
        return abs(actual - expected) <= Decimal("0.0001")
    return actual == expected


def _validate_olap_expectations(result: SmokeResult, expected: dict[str, Any]) -> None:
    expected_intents = list(expected.get("olap_intents") or [])
    result.expected_olap_intents = expected_intents
    if not expected_intents:
        return

    missing_intents = sorted(set(expected_intents) - set(result.olap_intents))
    result.olap_intent_match = not missing_intents
    if not result.olap_intent_match:
        result.fail(
            f"missing expected OLAP intents {missing_intents}; got {result.olap_intents}",
            "olap_intent_mismatch",
        )


def _validate_required_sql_patterns(result: SmokeResult, expected: dict[str, Any]) -> None:
    pattern_expectations = _sql_pattern_expectations(expected)
    result.expected_sql_patterns = [pattern for pattern, _ in pattern_expectations]
    if not pattern_expectations:
        return

    sql_text = _result_sql_text(result)
    missing_patterns = [
        pattern
        for pattern, case_sensitive in pattern_expectations
        if not _sql_pattern_matches(pattern, sql_text, case_sensitive=case_sensitive)
    ]
    result.sql_pattern_match = not missing_patterns
    if missing_patterns:
        result.fail(
            f"missing required SQL patterns: {missing_patterns}",
            "sql_generation_mismatch",
        )


def _sql_pattern_expectations(expected: dict[str, Any]) -> list[tuple[str, bool]]:
    pattern_expectations = []
    for item in expected.get("required_sql_patterns") or []:
        if isinstance(item, str):
            pattern_expectations.append((item, False))
            continue
        if isinstance(item, dict) and item.get("pattern"):
            pattern_expectations.append((str(item["pattern"]), bool(item.get("case_sensitive"))))
    return pattern_expectations


def _sql_pattern_matches(pattern: str, sql_text: str, *, case_sensitive: bool) -> bool:
    flags = re.DOTALL if case_sensitive else re.IGNORECASE | re.DOTALL
    return re.search(pattern, sql_text, flags=flags) is not None


def _result_sql_text(result: SmokeResult) -> str:
    return "\n".join(part for part in (result.generated_sql, result.normalized_sql) if part)


def _validate_plan_hint_expectation(result: SmokeResult, expected: dict[str, Any]) -> None:
    plan_hints_exist = expected.get("plan_hints_exist")
    if plan_hints_exist is None:
        return

    result.plan_hint_match = bool(result.plan_hints) == bool(plan_hints_exist)
    if not result.plan_hint_match:
        result.fail(
            f"expected plan_hints_exist {plan_hints_exist}, got {bool(result.plan_hints)}",
            "performance_mismatch",
        )


DUCKDB_SYNTAX_MARKERS = ("date_trunc(", "::", "read_csv(", "read_json(", "read_parquet(")


def _validate_dialect_hints(result: SmokeResult, expected: dict[str, Any]) -> None:
    hints = expected.get("dialect_hints") or []
    if not hints:
        return
    sql_text = _result_sql_text(result)
    normalized_sql = sql_text.casefold()
    for hint in hints:
        if not isinstance(hint, dict):
            continue
        function_name = hint.get("function")
        if function_name and function_name.casefold() not in normalized_sql:
            result.fail(
                f"expected dialect function {function_name!r} in SQL",
                "dialect_mismatch",
            )
        if hint.get("no_duckdb_syntax"):
            markers = [marker for marker in DUCKDB_SYNTAX_MARKERS if marker in normalized_sql]
            if markers:
                result.fail(
                    f"unexpected DuckDB syntax markers for {result.datasource_dialect}: {markers}",
                    "dialect_mismatch",
                )


def _validate_subset(
    result: SmokeResult,
    label: str,
    expected: list[str],
    actual: list[str],
    error_category: str = "result_mismatch",
) -> None:
    missing = sorted(set(expected) - set(actual))
    if missing:
        result.fail(f"missing expected {label}: {missing}; actual={actual}", error_category)


def _validate_retrieval_subset(
    result: SmokeResult,
    label: str,
    expected: list[str],
    actual: list[str],
) -> None:
    if not expected:
        return
    missing = sorted(set(expected) - set(actual))
    result.retrieval_checks.append(
        RetrievalCheck(
            label=label,
            expected=sorted(set(expected)),
            actual=actual,
            missing=missing,
        )
    )
    if missing:
        result.fail(f"missing expected {label}: {missing}; actual={actual}", "retrieval_miss")


def _guard_error_category(stage: str | None) -> str:
    if stage == "syntax_guard":
        return "sql_invalid"
    if stage == "fanout_guard":
        return "fanout_risk"
    return "guard_blocked"


def _format_join_path(path: dict[str, Any]) -> str:
    return (
        f"{path.get('source_table')}.{path.get('source_column')}"
        f" -> {path.get('target_table')}.{path.get('target_column')}"
    )


def _format_value_hit(hit: dict[str, Any]) -> str:
    table_name = hit.get("table_name")
    column_name = hit.get("column_name")
    matched_value = hit.get("matched_value")
    if table_name and column_name and matched_value:
        return f"{matched_value} -> {table_name}.{column_name}"
    return str(matched_value or "")


def _print_results(
    results: list[SmokeResult],
    report_path: Path,
    provider_name: str,
    skipped_case_ids: list[str],
) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        details = (
            f"datasource={result.datasource_name} "
            f"category={result.error_category or '-'} guard={result.guard_stage}"
        )
        if result.repair_count:
            details += f" repairs={result.repair_count}"
        if result.row_count is not None:
            details += f" rows={result.row_count}"
        if result.elapsed_ms is not None:
            details += f" elapsed={_format_elapsed(result.elapsed_ms)}"
        print(f"[{status}] {result.case_id} ({details})")
        for message in result.messages:
            print(f"  - {message}")

    passed = sum(1 for result in results if result.passed)
    print(f"\n{passed}/{len(results)} smoke cases passed.")
    for _, group in _group_results_by_datasource(results):
        group_passed = sum(1 for result in group if result.passed)
        print(f"{_datasource_section_title(group)}: {group_passed}/{len(group)} passed.")
    if skipped_case_ids:
        print(f"skipped {len(skipped_case_ids)} cases for provider={provider_name}.")
    summary = _summary_metrics(results)
    print(
        "focused context: "
        f"flags_off_avg={summary['avg_flags_off_context_chars']} chars, "
        f"avg={summary['avg_focused_context_chars']} chars, "
        f"avg_delta={summary['avg_flags_on_context_delta']:+d}, "
        f"full={summary['full_context_chars']} chars, "
        f"avg_reduction={_format_percent(summary['avg_context_reduction_ratio'])}, "
        f"fallback={summary['fallback_cases']}/{len(results)}, "
        f"repair_cases={summary['repair_cases']}/{len(results)}, "
        f"avg_elapsed={_format_elapsed(summary['avg_elapsed_ms'])}"
    )
    print(f"report: {report_path}")


def _write_report(
    path: Path,
    results: list[SmokeResult],
    provider_name: str,
    skipped_case_ids: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_report(results, provider_name=provider_name, skipped_case_ids=skipped_case_ids),
        encoding="utf-8",
    )


def _write_vector_compare_report(
    path: Path,
    *,
    rule_results: list[SmokeResult],
    vector_results: list[SmokeResult],
    provider_name: str,
    skipped_case_ids: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_vector_compare_report(
            rule_results=rule_results,
            vector_results=vector_results,
            provider_name=provider_name,
            skipped_case_ids=skipped_case_ids,
        ),
        encoding="utf-8",
    )


def _print_vector_compare_results(
    rule_results: list[SmokeResult],
    vector_results: list[SmokeResult],
    report_path: Path,
) -> None:
    rule_summary = _summary_metrics(rule_results)
    vector_summary = _summary_metrics(vector_results)
    value_stats = _value_recall_stats(vector_results)
    print(
        "rule-only: "
        f"{rule_summary['passed_cases']}/{rule_summary['total_cases']} passed, "
        f"fallback={rule_summary['fallback_cases']}/{rule_summary['total_cases']}, "
        f"avg_context={rule_summary['avg_focused_context_chars']} chars"
    )
    print(
        "rule+vector: "
        f"{vector_summary['passed_cases']}/{vector_summary['total_cases']} passed, "
        f"fallback={vector_summary['fallback_cases']}/{vector_summary['total_cases']}, "
        f"avg_context={vector_summary['avg_focused_context_chars']} chars, "
        f"value_recall={value_stats['hits']}/{value_stats['expected']}"
    )
    print(f"index status: {_format_distribution(_index_status_distribution(vector_results))}")
    print(f"report: {report_path}")


def _render_vector_compare_report(
    *,
    rule_results: list[SmokeResult],
    vector_results: list[SmokeResult],
    provider_name: str,
    skipped_case_ids: list[str],
) -> str:
    rule_summary = _summary_metrics(rule_results)
    vector_summary = _summary_metrics(vector_results)
    value_stats = _value_recall_stats(vector_results)
    lines = [
        "# Vector Compare Report",
        "",
        "## Summary",
        "",
        f"- Provider: {provider_name}",
        f"- Cases: {rule_summary['total_cases']}",
        f"- Skipped cases: {len(skipped_case_ids)}",
        f"- Rule-only pass rate: {rule_summary['passed_cases']}/{rule_summary['total_cases']}",
        f"- Rule+Vector pass rate: {vector_summary['passed_cases']}/{vector_summary['total_cases']}",
        f"- Rule-only fallback: {rule_summary['fallback_cases']}/{rule_summary['total_cases']}",
        f"- Rule+Vector fallback: {vector_summary['fallback_cases']}/{vector_summary['total_cases']}",
        f"- Rule-only avg focused context: {rule_summary['avg_focused_context_chars']} chars",
        f"- Rule+Vector avg focused context: {vector_summary['avg_focused_context_chars']} chars",
        f"- Value Recall hit rate: {value_stats['hits']}/{value_stats['expected']} ({_format_percent(_safe_rate(value_stats['hits'], value_stats['expected']))})",
        f"- Vector used: {sum(1 for result in vector_results if result.retrieval_vector_used)}/{len(vector_results)}",
        f"- Index status: {_format_distribution(_index_status_distribution(vector_results))}",
        "",
        "## Case Comparison",
        "",
        "| Case | Rule | Vector | Rule Fallback | Vector Fallback | Vector Status | Value Hits | Rule Chars | Vector Chars | Delta |",
        "|------|------|--------|---------------|-----------------|---------------|------------|------------|--------------|-------|",
    ]
    for rule_result, vector_result in zip(rule_results, vector_results, strict=True):
        delta = _context_delta(rule_result, vector_result)
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(rule_result.case_id),
                    "PASS" if rule_result.passed else "FAIL",
                    "PASS" if vector_result.passed else "FAIL",
                    str(rule_result.retrieval_fallback_used),
                    str(vector_result.retrieval_fallback_used),
                    _md_cell(vector_result.retrieval_index_status or "-"),
                    _md_cell(", ".join(vector_result.retrieval_value_hits) or "-"),
                    str(rule_result.focused_context_chars),
                    str(vector_result.focused_context_chars),
                    delta,
                ]
            )
            + " |"
        )

    lines.extend(["", "## Vector Failures", ""])
    failures = [result for result in vector_results if not result.passed]
    if not failures:
        lines.append("No vector-mode failures.")
    else:
        for result in failures:
            lines.extend([f"### {result.case_id}", ""])
            lines.extend(
                [
                    f"- Category: {result.error_category or '-'}",
                    f"- Index status: {result.retrieval_index_status or '-'}",
                    f"- Stale reason: {result.retrieval_stale_reason or '-'}",
                    f"- Value hits: {', '.join(result.retrieval_value_hits) or '-'}",
                ]
            )
            for message in result.messages:
                lines.append(f"- {message}")
            lines.append("")

    lines.extend(["", "## Skipped Cases", ""])
    if skipped_case_ids:
        for case_id in skipped_case_ids:
            lines.append(f"- {case_id}")
    else:
        lines.append("No skipped cases.")
    return "\n".join(lines).rstrip() + "\n"


def _retrieval_calibration_row(threshold: float, results: list[SmokeResult]) -> dict[str, Any]:
    recovery_cases = [
        result
        for result in results
        if "missing_join_path" in result.tags or "missing_dimension" in result.tags
    ]
    fallback_path_cases = [
        result for result in results if "dangling_no_fact" in result.tags
    ]
    high_conf_cases = [
        result
        for result in results
        if not _is_retrieval_closeout_case(result)
        and _reference_coverage(result).get("band") == "high"
    ]
    high_conf_regressions = [
        result
        for result in high_conf_cases
        if (result.retrieval_coverage or {}).get("band") != "high"
        or (result.flags_on_context_delta not in (None, 0))
    ]
    return {
        "threshold": threshold,
        "total_cases": len(results),
        "passed_cases": sum(1 for result in results if result.passed),
        "recovery_cases": len(recovery_cases),
        "recovery_passed": sum(1 for result in recovery_cases if _recovery_path_succeeded(result)),
        "fallback_path_cases": len(fallback_path_cases),
        "fallback_path_passed": sum(1 for result in fallback_path_cases if _fallback_path_succeeded(result)),
        "high_conf_cases": len(high_conf_cases),
        "high_conf_regressions": len(high_conf_regressions),
        "high_conf_regression_cases": [result.case_id for result in high_conf_regressions],
        "avg_flags_on_context_delta": _average_int(
            [
                result.flags_on_context_delta
                for result in results
                if result.flags_on_context_delta is not None
            ]
        ),
        "fallback_cases": sum(1 for result in results if result.retrieval_fallback_used),
    }


def _recovery_path_succeeded(result: SmokeResult) -> bool:
    coverage = result.retrieval_coverage or {}
    return (
        coverage.get("band") == "high"
        and coverage.get("expanded") is True
        and coverage.get("fallback_used") is False
    )


def _fallback_path_succeeded(result: SmokeResult) -> bool:
    coverage = result.retrieval_coverage or {}
    return coverage.get("band") == "low" and coverage.get("fallback_used") is True


def _is_retrieval_closeout_case(result: SmokeResult) -> bool:
    return "retrieval_closeout" in result.tags


def _reference_coverage(result: SmokeResult) -> dict[str, Any]:
    return result.retrieval_reference_coverage or result.retrieval_pre_coverage or {}


def _render_retrieval_calibration_report(
    rows: list[dict[str, Any]],
    *,
    provider_name: str,
    skipped_case_ids: list[str],
    reference_threshold: float,
) -> str:
    lines = [
        "# Retrieval Calibration Report",
        "",
        "## Summary",
        "",
        f"- Provider: {provider_name}",
        f"- Skipped cases: {len(skipped_case_ids)}",
        f"- Reference holdout threshold: {reference_threshold}",
        "",
        "| Threshold | Passed | Recovery | Fallback Paths | High-conf regressions | Fallback cases | Avg context delta | Regression cases |",
        "|-----------|--------|----------|----------------|-----------------------|----------------|-------------------|------------------|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["threshold"]),
                    f"{row['passed_cases']}/{row['total_cases']}",
                    f"{row['recovery_passed']}/{row['recovery_cases']}",
                    f"{row['fallback_path_passed']}/{row['fallback_path_cases']}",
                    f"{row['high_conf_regressions']}/{row['high_conf_cases']}",
                    str(row["fallback_cases"]),
                    _format_signed_int(row["avg_flags_on_context_delta"]),
                    _md_cell(", ".join(row["high_conf_regression_cases"]) or "-"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Skipped Cases", ""])
    if skipped_case_ids:
        for case_id in skipped_case_ids:
            lines.append(f"- {case_id}")
    else:
        lines.append("No skipped cases.")
    return "\n".join(lines).rstrip() + "\n"


def _render_report(
    results: list[SmokeResult],
    provider_name: str,
    skipped_case_ids: list[str],
) -> str:
    summary = _summary_metrics(results)
    retrieval_stats = _retrieval_stats(results)
    error_distribution = _error_distribution(results)
    chart_distribution = _chart_distribution(results)
    phase65_stats = _phase65_stats(results)
    reference_stats = _reference_result_stats(results)
    datasource_groups = _group_results_by_datasource(results)
    lines = [
        "# Smoke Eval Report",
        "",
        "## Summary",
        "",
        f"- Cases: {summary['total_cases']}",
        f"- Passed: {summary['passed_cases']}/{summary['total_cases']}",
        f"- Normal cases: {summary['normal_cases']}",
        f"- Safety cases: {summary['safety_cases']}",
        f"- Provider: {provider_name}",
        f"- Skipped cases: {len(skipped_case_ids)}",
        f"- Reference result matches: {reference_stats['matches']}/{reference_stats['checked']} checked",
        f"- Fallback used: {summary['fallback_cases']}/{summary['total_cases']}",
        f"- Repair cases: {summary['repair_cases']}/{summary['total_cases']}",
        f"- Total repair attempts: {summary['total_repair_attempts']}",
        f"- Full schema context chars: {summary['full_context_chars']}",
        f"- Avg flags-off focused context chars: {summary['avg_flags_off_context_chars']}",
        f"- Avg focused context chars: {summary['avg_focused_context_chars']}",
        f"- Avg flags-on context delta: {summary['avg_flags_on_context_delta']:+d}",
        f"- Avg focused context reduction: {_format_percent(summary['avg_context_reduction_ratio'])}",
        f"- Avg elapsed: {_format_elapsed(summary['avg_elapsed_ms'])}",
        f"- Chart recommendations: {_format_distribution(chart_distribution)}",
        "",
        "## Datasource Summary",
        "",
        "| Datasource | Dialect | Cases | Passed | Avg elapsed |",
        "|------------|---------|-------|--------|-------------|",
    ]
    for _, group in datasource_groups:
        group_summary = _summary_metrics(group)
        first = group[0]
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(first.datasource_display_name),
                    _md_cell(first.datasource_dialect),
                    str(group_summary["total_cases"]),
                    f"{group_summary['passed_cases']}/{group_summary['total_cases']}",
                    _format_elapsed(group_summary["avg_elapsed_ms"]),
                ]
            )
            + " |"
        )

    if phase65_stats["cases"] > 0:
        lines.extend(
            [
                "",
                "## Phase 6.5 OLAP Analytics",
                "",
                "| Datasource | Cases | OLAP Intent | YoY/MoM SQL | TopN SQL | Moving Avg SQL | Chart | Plan Hints |",
                "|------------|-------|-------------|-------------|----------|----------------|-------|------------|",
            ]
        )
        for _, group in datasource_groups:
            group_stats = _phase65_stats(group)
            if group_stats["cases"] == 0:
                continue
            first = group[0]
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(first.datasource_display_name),
                        str(group_stats["cases"]),
                        _format_check_rate(group_stats["olap_intent"]),
                        _format_check_rate(group_stats["yoy_mom_sql"]),
                        _format_check_rate(group_stats["topn_sql"]),
                        _format_check_rate(group_stats["moving_avg_sql"]),
                        _format_check_rate(group_stats["chart"]),
                        _format_check_rate(group_stats["plan_hints"]),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
        "",
        "## Error Distribution",
        "",
        "| Category | Count | Cases |",
        "|----------|-------|-------|",
        ]
    )
    if error_distribution:
        for category, case_ids in error_distribution.items():
            lines.append(
                f"| {_md_cell(category)} | {len(case_ids)} | {_md_cell(', '.join(case_ids))} |"
            )
    else:
        lines.append("| n/a | 0 | - |")

    lines.extend(["", "## Skipped Cases", ""])
    if skipped_case_ids:
        for case_id in skipped_case_ids:
            lines.append(f"- {case_id}")
    else:
        lines.append("No skipped cases.")

    lines.extend(
        [
            "",
            "## Retrieval Expected Hits",
            "",
            "| Asset | Hit | Expected | Rate |",
            "|-------|-----|----------|------|",
        ]
    )
    if retrieval_stats:
        for label, stats in retrieval_stats.items():
            lines.append(
                f"| {_md_cell(label)} | {stats['hits']} | {stats['expected']} | "
                f"{_format_percent(_safe_rate(stats['hits'], stats['expected']))} |"
            )
    else:
        lines.append("| n/a | 0 | 0 | n/a |")

    lines.extend(
        [
            "",
            "## Case Results",
            "",
        ]
    )
    for _, group in datasource_groups:
        lines.extend(
            [
                f"### {_datasource_section_title(group)}",
                "",
                "| Case | Status | Type | Category | Reference | Fallback | Coverage | Repairs | Elapsed | Focused Chars (off→on) | Reduction | Guard | Rows | Chart | SQL |",
                "|------|--------|------|----------|-----------|----------|----------|---------|---------|---------------|-----------|-------|------|-------|-----|",
            ]
        )
        for result in group:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(result.case_id),
                        "PASS" if result.passed else "FAIL",
                        _md_cell(result.case_type or "-"),
                        _md_cell(result.error_category or "-"),
                        _reference_result_label(result.reference_result_match),
                        str(result.retrieval_fallback_used),
                        _md_cell(_format_coverage_transition(result)),
                        str(result.repair_count),
                        _format_elapsed(result.elapsed_ms),
                        _format_context_chars(result),
                        _format_percent(result.context_reduction_ratio),
                        _md_cell(result.guard_stage or "-"),
                        str(result.row_count) if result.row_count is not None else "-",
                        _md_cell(result.chart_type or "-"),
                        _md_cell(_short_sql(result.generated_sql) or "-"),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.extend(["", "## Failure Details", ""])
    failures = [result for result in results if not result.passed]
    if not failures:
        lines.append("No failures.")
    else:
        for result in failures:
            lines.extend([f"### {result.case_id}", ""])
            lines.extend(
                [
                    f"- Category: {result.error_category or '-'}",
                    f"- Datasource: {result.datasource_display_name} ({result.datasource_name})",
                    f"- Elapsed: {_format_elapsed(result.elapsed_ms)}",
                    f"- Guard: {result.guard_stage or '-'}",
                    f"- Guard reason: {result.guard_reason or '-'}",
                    f"- Repair count: {result.repair_count}",
                    f"- Reference result match: {_reference_result_label(result.reference_result_match)}",
                    f"- Chart: {result.chart_type or '-'}",
                    f"- Retrieved tables: {', '.join(result.retrieval_tables) or '-'}",
                    f"- Retrieved metrics: {', '.join(result.retrieval_metrics) or '-'}",
                ]
            )
            for message in result.messages:
                lines.append(f"- {message}")
            if result.generated_sql:
                lines.extend(["", "Generated SQL:", "", "```sql", result.generated_sql, "```"])
            if result.normalized_sql:
                lines.extend(["", "Normalized SQL:", "", "```sql", result.normalized_sql, "```"])
            lines.append("")

    lines.extend(["", "## Retrieval Details", ""])
    for _, group in datasource_groups:
        lines.extend([f"### {_datasource_section_title(group)}", ""])
        for result in group:
            lines.extend(
                [
                    f"#### {result.case_id}",
                    "",
                    f"- Question: {result.question}",
                    f"- Tables: {', '.join(result.retrieval_tables) or '-'}",
                    f"- Columns: {', '.join(result.retrieval_columns) or '-'}",
                    f"- Metrics: {', '.join(result.retrieval_metrics) or '-'}",
                    f"- Verified queries: {', '.join(result.retrieval_verified_queries) or '-'}",
                    f"- Coverage: {_format_coverage_transition(result)}",
                    f"- Focused context chars: {_format_context_chars(result)}",
                ]
            )
            if result.retrieval_checks:
                lines.append("- Expected retrieval checks:")
                for check in result.retrieval_checks:
                    status = "PASS" if not check.missing else "FAIL"
                    lines.append(
                        f"  - {check.label}: {status}; expected={check.expected}; missing={check.missing}"
                    )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _summary_metrics(results: list[SmokeResult]) -> dict[str, Any]:
    focused_lengths = [
        result.focused_context_chars
        for result in results
        if result.focused_context_chars is not None
    ]
    flags_off_lengths = [
        result.flags_off_context_chars
        for result in results
        if result.flags_off_context_chars is not None
    ]
    flags_on_deltas = [
        result.flags_on_context_delta
        for result in results
        if result.flags_on_context_delta is not None
    ]
    reductions = [
        result.context_reduction_ratio
        for result in results
        if result.context_reduction_ratio is not None
    ]
    full_lengths = [
        result.full_context_chars
        for result in results
        if result.full_context_chars is not None
    ]
    return {
        "total_cases": len(results),
        "passed_cases": sum(1 for result in results if result.passed),
        "normal_cases": sum(1 for result in results if result.case_type == "normal"),
        "safety_cases": sum(1 for result in results if result.case_type == "safety"),
        "fallback_cases": sum(1 for result in results if result.retrieval_fallback_used),
        "repair_cases": sum(1 for result in results if result.repair_count > 0),
        "total_repair_attempts": sum(result.repair_count for result in results),
        "full_context_chars": _average_int(full_lengths),
        "avg_flags_off_context_chars": _average_int(flags_off_lengths),
        "avg_focused_context_chars": _average_int(focused_lengths),
        "avg_flags_on_context_delta": _average_int(flags_on_deltas),
        "avg_context_reduction_ratio": _average_float(reductions),
        "avg_elapsed_ms": _average_int(
            [result.elapsed_ms for result in results if result.elapsed_ms is not None]
        ),
    }


def _reference_result_stats(results: list[SmokeResult]) -> dict[str, int]:
    checked = [result for result in results if result.reference_result_match is not None]
    return {
        "checked": len(checked),
        "matches": sum(1 for result in checked if result.reference_result_match is True),
    }


def _reference_result_label(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "n/a"


def _group_results_by_datasource(results: list[SmokeResult]) -> list[tuple[str, list[SmokeResult]]]:
    groups: dict[str, list[SmokeResult]] = {}
    for result in results:
        groups.setdefault(result.datasource_name, []).append(result)
    return list(groups.items())


def _datasource_section_title(results: list[SmokeResult]) -> str:
    if not results:
        return "Unknown - 0 cases"
    first = results[0]
    return f"{first.datasource_display_name} - {len(results)} cases"


def _value_recall_stats(results: list[SmokeResult]) -> dict[str, int]:
    stats = {"hits": 0, "expected": 0}
    for result in results:
        for check in result.retrieval_checks:
            if check.label != "retrieval value hits":
                continue
            expected = set(check.expected)
            actual = set(check.actual)
            stats["hits"] += len(expected & actual)
            stats["expected"] += len(expected)
    return stats


def _index_status_distribution(results: list[SmokeResult]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for result in results:
        status = result.retrieval_index_status or "n/a"
        distribution[status] = distribution.get(status, 0) + 1
    return distribution


def _context_delta(rule_result: SmokeResult, vector_result: SmokeResult) -> str:
    if rule_result.focused_context_chars is None or vector_result.focused_context_chars is None:
        return "-"
    delta = vector_result.focused_context_chars - rule_result.focused_context_chars
    return f"{delta:+d}"


def _retrieval_stats(results: list[SmokeResult]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for result in results:
        for check in result.retrieval_checks:
            expected = set(check.expected)
            actual = set(check.actual)
            label_stats = stats.setdefault(check.label, {"hits": 0, "expected": 0})
            label_stats["hits"] += len(expected & actual)
            label_stats["expected"] += len(expected)
    return stats


def _error_distribution(results: list[SmokeResult]) -> dict[str, list[str]]:
    distribution: dict[str, list[str]] = {}
    for result in results:
        if result.passed:
            continue
        category = result.error_category or "unknown"
        distribution.setdefault(category, []).append(result.case_id)
    return distribution


def _chart_distribution(results: list[SmokeResult]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for result in results:
        if result.chart_type is None:
            continue
        distribution[result.chart_type] = distribution.get(result.chart_type, 0) + 1
    return distribution


def _phase65_stats(results: list[SmokeResult]) -> dict[str, Any]:
    phase_results = [result for result in results if "phase65" in result.tags]
    return {
        "cases": len(phase_results),
        "olap_intent": _boolean_check_stats(
            [result.olap_intent_match for result in phase_results]
        ),
        "yoy_mom_sql": _boolean_check_stats(
            [
                result.sql_pattern_match
                for result in phase_results
                if _expects_phase65_intent(result, "yoy_mom")
            ]
        ),
        "topn_sql": _boolean_check_stats(
            [
                result.sql_pattern_match
                for result in phase_results
                if _expects_phase65_intent(result, "topn")
            ]
        ),
        "moving_avg_sql": _boolean_check_stats(
            [
                result.sql_pattern_match
                for result in phase_results
                if _expects_phase65_intent(result, "moving_avg")
            ]
        ),
        "chart": _boolean_check_stats([result.chart_match for result in phase_results]),
        "plan_hints": _boolean_check_stats(
            [result.plan_hint_match for result in phase_results]
        ),
    }


def _expects_phase65_intent(result: SmokeResult, intent: str) -> bool:
    return intent in result.expected_olap_intents


def _boolean_check_stats(values: list[bool | None]) -> dict[str, int]:
    expected_values = [value for value in values if value is not None]
    return {
        "hits": sum(1 for value in expected_values if value),
        "expected": len(expected_values),
    }


def _format_check_rate(stats: dict[str, int]) -> str:
    expected = stats["expected"]
    if expected == 0:
        return "n/a"
    hits = stats["hits"]
    return f"{hits}/{expected} ({_format_percent(_safe_rate(hits, expected))})"


def _format_distribution(distribution: dict[str, int]) -> str:
    if not distribution:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in sorted(distribution.items()))


def _average_int(values: list[int]) -> int:
    if not values:
        return 0
    return round(sum(values) / len(values))


def _average_float(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _format_elapsed(value: int | None) -> str:
    if value is None:
        return "-"
    if value >= 1000:
        return f"{value / 1000:.1f}s"
    return f"{value}ms"


def _format_retrieval_coverage(value: dict[str, Any] | None) -> str:
    if not value:
        return "-"
    band = value.get("band", "-")
    score = value.get("score")
    expanded = value.get("expanded")
    fallback_used = value.get("fallback_used")
    score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else "-"
    return f"{band}/{score_text} expanded={expanded} fallback={fallback_used}"


def _format_coverage_transition(result: SmokeResult) -> str:
    post = _format_retrieval_coverage(result.retrieval_coverage)
    pre = _format_retrieval_coverage(result.retrieval_pre_coverage)
    if pre == "-":
        return post
    return f"{pre} -> {post}"


def _format_context_chars(result: SmokeResult) -> str:
    if result.focused_context_chars is None:
        return "-"
    if result.flags_off_context_chars is None or result.flags_on_context_delta is None:
        return str(result.focused_context_chars)
    return (
        f"{result.flags_off_context_chars}->{result.focused_context_chars} "
        f"({_format_signed_int(result.flags_on_context_delta)})"
    )


def _format_signed_int(value: int) -> str:
    return f"{value:+d}"


def _short_sql(value: str | None, limit: int = 140) -> str:
    if not value:
        return ""
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    sys.exit(main())
