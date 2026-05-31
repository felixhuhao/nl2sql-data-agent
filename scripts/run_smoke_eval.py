from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from backend.app.agent.nodes import generate_sql_node
from backend.app.agent.repair import iter_sql_repair_events
from backend.app.agent.state import AgentState
from backend.app.config import get_settings
from backend.app.core.deepseek_provider import DeepSeekProvider
from backend.app.core.llm_provider import (
    LLMProvider,
    MockLLMProvider,
    SQLGenerationRequest,
    SQLGenerationResult,
)
from backend.app.execution.runner import execute_guarded_sql
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.metadata.service import build_focused_context_from_retrieval, build_schema_context
from backend.app.sql_guard import build_default_guard_scope, guard_sql
from backend.app.visualization.recommender import recommend_chart


@dataclass
class RetrievalCheck:
    label: str
    expected: list[str]
    actual: list[str]
    missing: list[str]


@dataclass
class SmokeResult:
    case_id: str
    case_type: str
    question: str
    passed: bool = True
    messages: list[str] = field(default_factory=list)
    sql: str | None = None
    guard_stage: str | None = None
    row_count: int | None = None
    retrieval_fallback_used: bool | None = None
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
    full_context_chars: int | None = None
    context_reduction_ratio: float | None = None
    error_category: str | None = None
    generated_sql: str | None = None
    normalized_sql: str | None = None
    chart_type: str | None = None
    guard_reason: str | None = None
    elapsed_ms: int | None = None
    repair_count: int = 0

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
    args = parser.parse_args()

    cases = _load_cases(Path(args.cases_path))
    scope = build_default_guard_scope()
    try:
        provider = _create_provider(args.provider)
        selected_cases, skipped_case_ids = _filter_cases(cases, provider_name=args.provider)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    full_schema_context = build_schema_context()
    if args.vector_compare:
        return _run_vector_compare(
            selected_cases=selected_cases,
            scope=scope,
            provider=provider,
            full_schema_context=full_schema_context,
            provider_name=args.provider,
            report_path=Path(args.report_path),
            skipped_case_ids=skipped_case_ids,
        )

    results = [
        _run_case(
            case,
            scope,
            provider,
            full_schema_context,
            provider_name=args.provider,
            use_vector=False,
        )
        for case in selected_cases
    ]
    report_path = Path(args.report_path)
    _write_report(report_path, results, provider_name=args.provider, skipped_case_ids=skipped_case_ids)
    _print_results(results, report_path, provider_name=args.provider, skipped_case_ids=skipped_case_ids)
    return 0 if all(result.passed for result in results) else 1


def _run_vector_compare(
    *,
    selected_cases: list[dict[str, Any]],
    scope,
    provider: LLMProvider,
    full_schema_context: str,
    provider_name: str,
    report_path: Path,
    skipped_case_ids: list[str],
) -> int:
    rule_results = [
        _run_case(
            case,
            scope,
            provider,
            full_schema_context,
            provider_name=provider_name,
            use_vector=False,
        )
        for case in selected_cases
    ]
    vector_results = [
        _run_case(
            case,
            scope,
            provider,
            full_schema_context,
            provider_name=provider_name,
            use_vector=True,
            validate_vector_expectations=True,
        )
        for case in selected_cases
    ]
    _write_vector_compare_report(
        report_path,
        rule_results=rule_results,
        vector_results=vector_results,
        provider_name=provider_name,
        skipped_case_ids=skipped_case_ids,
    )
    _print_vector_compare_results(rule_results, vector_results, report_path)
    return 0 if all(result.passed for result in [*rule_results, *vector_results]) else 1


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
) -> tuple[list[dict[str, Any]], list[str]]:
    selected_cases = []
    skipped_case_ids = []
    for case in cases:
        case_provider = case.get("provider", "both")
        if case_provider not in {"both", "mock", "real"}:
            raise ValueError(f"Unknown case provider {case_provider!r} in {case['id']}.")
        if _case_matches_provider(case_provider, provider_name):
            selected_cases.append(case)
        else:
            skipped_case_ids.append(case["id"])
    return selected_cases, skipped_case_ids


def _case_matches_provider(case_provider: str, provider_name: str) -> bool:
    if case_provider == "both":
        return True
    if case_provider == "mock":
        return provider_name == "mock"
    return provider_name != "mock"


def _run_case(
    case: dict[str, Any],
    scope,
    provider: LLMProvider,
    full_schema_context: str,
    provider_name: str,
    use_vector: bool | None = None,
    validate_vector_expectations: bool = False,
) -> SmokeResult:
    result = SmokeResult(
        case_id=case["id"],
        case_type=case.get("type", ""),
        question=case["question"],
    )
    started_at = time.perf_counter()
    try:
        expected = case.get("expected", {})
        try:
            retrieval_result = retrieve_metadata_assets(case["question"], use_vector=use_vector)
            _record_retrieval_result(result, retrieval_result)
            _validate_retrieval(result, retrieval_result, expected.get("retrieval") or {})
            if validate_vector_expectations:
                _validate_vector_retrieval(result, expected.get("vector") or {})
            schema_context = build_focused_context_from_retrieval(retrieval_result)
        except Exception as exc:
            result.fail(f"retrieval/context failed: {exc}", "retrieval_miss")
            return result

        result.focused_context_chars = len(schema_context)
        result.full_context_chars = len(full_schema_context)
        result.context_reduction_ratio = _context_reduction_ratio(
            focused_chars=result.focused_context_chars,
            full_chars=result.full_context_chars,
        )

        state = AgentState(question=case["question"], schema_context=schema_context)
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
            _run_guard_only_case(result=result, state=state, scope=scope, expected=expected)
            return result

        try:
            repair_events = list(
                iter_sql_repair_events(
                    state,
                    provider=case_provider,
                    scope_builder=lambda: scope,
                    executor=execute_guarded_sql,
                )
            )
        except Exception as exc:
            result.fail(f"SQL repair workflow failed: {exc}", "repair_error")
            return result

        _record_state_result(result, state)
        _validate_repair_expectations(result, expected)

        final_event = repair_events[-1] if repair_events else None
        if expected.get("repair_should_fail"):
            _validate_expected_repair_failure(result, final_event, expected)
            return result

        if final_event and final_event.step == "error":
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

        chart_recommendation = recommend_chart(state.query_result)
        result.chart_type = chart_recommendation.chart_type

        _validate_normal_case(
            result=result,
            expected=expected,
            matched_query_id=state.matched_query_id,
            query_result=state.query_result,
            explainability=state.explainability or {},
            chart_type=chart_recommendation.chart_type,
            provider_name=provider_name,
        )
        return result
    finally:
        result.elapsed_ms = round((time.perf_counter() - started_at) * 1000)


def _record_retrieval_result(result: SmokeResult, retrieval_result: dict[str, Any]) -> None:
    retrieval_meta = retrieval_result.get("retrieval_meta") or {}
    result.retrieval_fallback_used = bool(retrieval_result.get("fallback_used"))
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


def _run_guard_only_case(
    *,
    result: SmokeResult,
    state: AgentState,
    scope,
    expected: dict[str, Any],
) -> None:
    try:
        guard_result = guard_sql(state.sql or "", scope=scope)
    except Exception as exc:
        result.fail(f"SQL Guard failed: {exc}", "guard_blocked")
        return

    state.guard_result = guard_result
    result.guard_stage = guard_result.stage
    result.guard_reason = guard_result.reason
    result.normalized_sql = guard_result.normalized_sql
    _validate_safety_case(result, guard_result, expected)
    _validate_repair_expectations(result, expected)


def _record_state_result(result: SmokeResult, state: AgentState) -> None:
    result.sql = state.sql
    result.generated_sql = state.sql
    result.repair_count = len(state.repair_history)
    if state.guard_result is not None:
        result.guard_stage = state.guard_result.stage
        result.guard_reason = state.guard_result.reason
        result.normalized_sql = state.guard_result.normalized_sql
    if state.query_result is not None:
        result.row_count = state.query_result.row_count


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
) -> None:
    if provider_name == "mock" and matched_query_id != expected.get("matched_query_id"):
        result.fail(
            f"expected matched_query_id {expected.get('matched_query_id')}, got {matched_query_id}",
            "result_mismatch",
        )

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
    if query_result.columns != expected_columns:
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
        details = f"category={result.error_category or '-'} guard={result.guard_stage}"
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
    if skipped_case_ids:
        print(f"skipped {len(skipped_case_ids)} cases for provider={provider_name}.")
    summary = _summary_metrics(results)
    print(
        "focused context: "
        f"avg={summary['avg_focused_context_chars']} chars, "
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


def _render_report(
    results: list[SmokeResult],
    provider_name: str,
    skipped_case_ids: list[str],
) -> str:
    summary = _summary_metrics(results)
    retrieval_stats = _retrieval_stats(results)
    error_distribution = _error_distribution(results)
    chart_distribution = _chart_distribution(results)
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
        f"- Fallback used: {summary['fallback_cases']}/{summary['total_cases']}",
        f"- Repair cases: {summary['repair_cases']}/{summary['total_cases']}",
        f"- Total repair attempts: {summary['total_repair_attempts']}",
        f"- Full schema context chars: {summary['full_context_chars']}",
        f"- Avg focused context chars: {summary['avg_focused_context_chars']}",
        f"- Avg focused context reduction: {_format_percent(summary['avg_context_reduction_ratio'])}",
        f"- Avg elapsed: {_format_elapsed(summary['avg_elapsed_ms'])}",
        f"- Chart recommendations: {_format_distribution(chart_distribution)}",
        "",
        "## Error Distribution",
        "",
        "| Category | Count | Cases |",
        "|----------|-------|-------|",
    ]
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
            "| Case | Status | Type | Category | Fallback | Repairs | Elapsed | Focused Chars | Reduction | Guard | Rows | Chart | SQL |",
            "|------|--------|------|----------|----------|---------|---------|---------------|-----------|-------|------|-------|-----|",
        ]
    )
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(result.case_id),
                    "PASS" if result.passed else "FAIL",
                    _md_cell(result.case_type or "-"),
                    _md_cell(result.error_category or "-"),
                    str(result.retrieval_fallback_used),
                    str(result.repair_count),
                    _format_elapsed(result.elapsed_ms),
                    str(result.focused_context_chars),
                    _format_percent(result.context_reduction_ratio),
                    _md_cell(result.guard_stage or "-"),
                    str(result.row_count) if result.row_count is not None else "-",
                    _md_cell(result.chart_type or "-"),
                    _md_cell(_short_sql(result.generated_sql) or "-"),
                ]
            )
            + " |"
        )

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
                    f"- Elapsed: {_format_elapsed(result.elapsed_ms)}",
                    f"- Guard: {result.guard_stage or '-'}",
                    f"- Guard reason: {result.guard_reason or '-'}",
                    f"- Repair count: {result.repair_count}",
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
    for result in results:
        lines.extend(
            [
                f"### {result.case_id}",
                "",
                f"- Question: {result.question}",
                f"- Tables: {', '.join(result.retrieval_tables) or '-'}",
                f"- Columns: {', '.join(result.retrieval_columns) or '-'}",
                f"- Metrics: {', '.join(result.retrieval_metrics) or '-'}",
                f"- Verified queries: {', '.join(result.retrieval_verified_queries) or '-'}",
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
        "full_context_chars": full_lengths[0] if full_lengths else 0,
        "avg_focused_context_chars": _average_int(focused_lengths),
        "avg_context_reduction_ratio": _average_float(reductions),
        "avg_elapsed_ms": _average_int(
            [result.elapsed_ms for result in results if result.elapsed_ms is not None]
        ),
    }


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
