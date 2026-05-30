from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from backend.app.agent.explainability import build_query_explainability
from backend.app.core.llm_provider import MockLLMProvider, SQLGenerationRequest
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
    retrieval_checks: list[RetrievalCheck] = field(default_factory=list)
    focused_context_chars: int | None = None
    full_context_chars: int | None = None
    context_reduction_ratio: float | None = None
    error_category: str | None = None
    generated_sql: str | None = None
    normalized_sql: str | None = None
    elapsed_ms: int | None = None

    def fail(self, message: str, error_category: str | None = None) -> None:
        self.passed = False
        self.messages.append(message)
        if error_category and self.error_category is None:
            self.error_category = error_category


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
    args = parser.parse_args()

    cases = _load_cases(Path(args.cases_path))
    scope = build_default_guard_scope()
    provider = MockLLMProvider()
    full_schema_context = build_schema_context()

    results = [_run_case(case, scope, provider, full_schema_context) for case in cases]
    report_path = Path(args.report_path)
    _write_report(report_path, results)
    _print_results(results, report_path)
    return 0 if all(result.passed for result in results) else 1


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("smoke case file must contain a list under 'cases'.")
    return cases


def _run_case(
    case: dict[str, Any],
    scope,
    provider: MockLLMProvider,
    full_schema_context: str,
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
            retrieval_result = retrieve_metadata_assets(case["question"])
            _record_retrieval_result(result, retrieval_result)
            _validate_retrieval(result, retrieval_result, expected.get("retrieval") or {})
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

        try:
            sql, matched_query_id = _resolve_sql(case, schema_context, provider)
        except Exception as exc:
            result.fail(f"SQL generation failed: {exc}", "sql_generation_error")
            return result

        result.sql = sql
        result.generated_sql = sql
        if not sql.strip():
            result.fail("SQL generation returned empty SQL.", "sql_generation_error")
            return result

        try:
            guard_result = guard_sql(sql, scope=scope)
        except Exception as exc:
            result.fail(f"SQL Guard failed: {exc}", "guard_blocked")
            return result

        result.guard_stage = guard_result.stage
        result.normalized_sql = guard_result.normalized_sql
        try:
            explainability = build_query_explainability(
                sql=guard_result.normalized_sql or sql,
                question=case["question"],
                guard_result=guard_result,
            )
        except Exception as exc:
            result.fail(f"explainability failed: {exc}", "result_mismatch")
            return result

        if expected.get("should_execute") is False:
            _validate_safety_case(result, guard_result, expected)
            return result

        if not guard_result.allowed:
            error_category = "sql_invalid" if guard_result.stage == "syntax_guard" else "guard_blocked"
            result.fail(
                f"expected execution, but Guard rejected SQL: {guard_result.reason}",
                error_category,
            )
            return result

        try:
            query_result = execute_guarded_sql(guard_result)
        except Exception as exc:
            result.fail(f"SQL execution failed: {exc}", "execution_error")
            return result

        result.row_count = query_result.row_count
        chart_recommendation = recommend_chart(query_result)

        _validate_normal_case(
            result=result,
            expected=expected,
            matched_query_id=matched_query_id,
            query_result=query_result,
            explainability=explainability,
            chart_type=chart_recommendation.chart_type,
        )
        return result
    finally:
        result.elapsed_ms = round((time.perf_counter() - started_at) * 1000)


def _record_retrieval_result(result: SmokeResult, retrieval_result: dict[str, Any]) -> None:
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


def _context_reduction_ratio(focused_chars: int, full_chars: int) -> float | None:
    if full_chars <= 0:
        return None
    return 1 - focused_chars / full_chars


def _resolve_sql(
    case: dict[str, Any],
    schema_context: str,
    provider: MockLLMProvider,
) -> tuple[str, str | None]:
    if case.get("mock_sql"):
        return case["mock_sql"], None

    generation = provider.generate_sql(
        SQLGenerationRequest(question=case["question"], schema_context=schema_context)
    )
    return generation.sql, generation.matched_query_id


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


def _validate_safety_case(result: SmokeResult, guard_result, expected: dict[str, Any]) -> None:
    if guard_result.allowed:
        result.fail("expected Guard rejection, but SQL was allowed", "guard_blocked")
        return

    expected_stage = expected.get("guard_stage")
    if expected_stage and guard_result.stage != expected_stage:
        result.fail(
            f"expected Guard stage {expected_stage}, got {guard_result.stage}",
            "guard_blocked",
        )

    reason_contains = expected.get("reason_contains")
    if reason_contains and reason_contains not in (guard_result.reason or ""):
        result.fail(
            f"expected reason to contain {reason_contains!r}, got {guard_result.reason!r}",
            "guard_blocked",
        )


def _validate_normal_case(
    result: SmokeResult,
    expected: dict[str, Any],
    matched_query_id: str | None,
    query_result,
    explainability: dict[str, Any],
    chart_type: str,
) -> None:
    if matched_query_id != expected.get("matched_query_id"):
        result.fail(
            f"expected matched_query_id {expected.get('matched_query_id')}, got {matched_query_id}",
            "result_mismatch",
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
            "result_mismatch",
        )

    _validate_subset(
        result,
        label="tables",
        expected=expected.get("required_tables") or [],
        actual=explainability.get("matched_tables") or [],
    )
    _validate_subset(
        result,
        label="columns",
        expected=expected.get("required_columns") or [],
        actual=explainability.get("matched_columns") or [],
    )
    _validate_subset(
        result,
        label="join paths",
        expected=expected.get("join_paths") or [],
        actual=[_format_join_path(path) for path in explainability.get("join_paths") or []],
    )


def _validate_subset(
    result: SmokeResult,
    label: str,
    expected: list[str],
    actual: list[str],
) -> None:
    missing = sorted(set(expected) - set(actual))
    if missing:
        result.fail(f"missing expected {label}: {missing}; actual={actual}", "result_mismatch")


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


def _format_join_path(path: dict[str, Any]) -> str:
    return (
        f"{path.get('source_table')}.{path.get('source_column')}"
        f" -> {path.get('target_table')}.{path.get('target_column')}"
    )


def _print_results(results: list[SmokeResult], report_path: Path) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        details = f"category={result.error_category or '-'} guard={result.guard_stage}"
        if result.row_count is not None:
            details += f" rows={result.row_count}"
        if result.elapsed_ms is not None:
            details += f" elapsed={_format_elapsed(result.elapsed_ms)}"
        print(f"[{status}] {result.case_id} ({details})")
        for message in result.messages:
            print(f"  - {message}")

    passed = sum(1 for result in results if result.passed)
    print(f"\n{passed}/{len(results)} smoke cases passed.")
    summary = _summary_metrics(results)
    print(
        "focused context: "
        f"avg={summary['avg_focused_context_chars']} chars, "
        f"full={summary['full_context_chars']} chars, "
        f"avg_reduction={_format_percent(summary['avg_context_reduction_ratio'])}, "
        f"fallback={summary['fallback_cases']}/{len(results)}, "
        f"avg_elapsed={_format_elapsed(summary['avg_elapsed_ms'])}"
    )
    print(f"report: {report_path}")


def _write_report(path: Path, results: list[SmokeResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_report(results), encoding="utf-8")


def _render_report(results: list[SmokeResult]) -> str:
    summary = _summary_metrics(results)
    retrieval_stats = _retrieval_stats(results)
    error_distribution = _error_distribution(results)
    lines = [
        "# Smoke Eval Report",
        "",
        "## Summary",
        "",
        f"- Cases: {summary['total_cases']}",
        f"- Passed: {summary['passed_cases']}/{summary['total_cases']}",
        f"- Normal cases: {summary['normal_cases']}",
        f"- Safety cases: {summary['safety_cases']}",
        f"- Fallback used: {summary['fallback_cases']}/{summary['total_cases']}",
        f"- Full schema context chars: {summary['full_context_chars']}",
        f"- Avg focused context chars: {summary['avg_focused_context_chars']}",
        f"- Avg focused context reduction: {_format_percent(summary['avg_context_reduction_ratio'])}",
        f"- Avg elapsed: {_format_elapsed(summary['avg_elapsed_ms'])}",
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
            "| Case | Status | Type | Category | Fallback | Elapsed | Focused Chars | Reduction | Guard | Rows | SQL |",
            "|------|--------|------|----------|----------|---------|---------------|-----------|-------|------|-----|",
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
                    _format_elapsed(result.elapsed_ms),
                    str(result.focused_context_chars),
                    _format_percent(result.context_reduction_ratio),
                    _md_cell(result.guard_stage or "-"),
                    str(result.row_count) if result.row_count is not None else "-",
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
        "full_context_chars": full_lengths[0] if full_lengths else 0,
        "avg_focused_context_chars": _average_int(focused_lengths),
        "avg_context_reduction_ratio": _average_float(reductions),
        "avg_elapsed_ms": _average_int(
            [result.elapsed_ms for result in results if result.elapsed_ms is not None]
        ),
    }


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
