from __future__ import annotations

import argparse
import sys
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
from backend.app.metadata.service import build_focused_context_from_retrieval
from backend.app.sql_guard import build_default_guard_scope, guard_sql
from backend.app.visualization.recommender import recommend_chart


@dataclass
class SmokeResult:
    case_id: str
    passed: bool = True
    messages: list[str] = field(default_factory=list)
    sql: str | None = None
    guard_stage: str | None = None
    row_count: int | None = None

    def fail(self, message: str) -> None:
        self.passed = False
        self.messages.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 1 smoke eval cases.")
    parser.add_argument(
        "cases_path",
        nargs="?",
        default="evals/smoke_cases.yaml",
        help="Path to smoke case YAML file.",
    )
    args = parser.parse_args()

    cases = _load_cases(Path(args.cases_path))
    scope = build_default_guard_scope()
    provider = MockLLMProvider()

    results = [_run_case(case, scope, provider) for case in cases]
    _print_results(results)
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
) -> SmokeResult:
    result = SmokeResult(case_id=case["id"])
    expected = case.get("expected", {})
    retrieval_result = retrieve_metadata_assets(case["question"])
    _validate_retrieval(result, retrieval_result, expected.get("retrieval") or {})
    schema_context = build_focused_context_from_retrieval(retrieval_result)

    sql, matched_query_id = _resolve_sql(case, schema_context, provider)
    result.sql = sql

    guard_result = guard_sql(sql, scope=scope)
    result.guard_stage = guard_result.stage
    explainability = build_query_explainability(
        sql=guard_result.normalized_sql or sql,
        question=case["question"],
        guard_result=guard_result,
    )

    if expected.get("should_execute") is False:
        _validate_safety_case(result, guard_result, expected)
        return result

    if not guard_result.allowed:
        result.fail(f"expected execution, but Guard rejected SQL: {guard_result.reason}")
        return result

    query_result = execute_guarded_sql(guard_result)
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
            f"got {retrieval_result.get('fallback_used')}"
        )

    _validate_subset(
        result,
        label="retrieval tables",
        expected=expected.get("required_tables") or [],
        actual=[table["table_name"] for table in retrieval_result.get("tables") or []],
    )
    _validate_subset(
        result,
        label="retrieval columns",
        expected=expected.get("required_columns") or [],
        actual=[
            f"{column['table_name']}.{column['column_name']}"
            for column in retrieval_result.get("columns") or []
        ],
    )
    _validate_subset(
        result,
        label="retrieval metrics",
        expected=expected.get("required_metrics") or [],
        actual=[metric["name"] for metric in retrieval_result.get("metrics") or []],
    )
    _validate_subset(
        result,
        label="retrieval verified queries",
        expected=expected.get("required_verified_queries") or [],
        actual=[query["id"] for query in retrieval_result.get("verified_queries") or []],
    )


def _validate_safety_case(result: SmokeResult, guard_result, expected: dict[str, Any]) -> None:
    if guard_result.allowed:
        result.fail("expected Guard rejection, but SQL was allowed")
        return

    expected_stage = expected.get("guard_stage")
    if expected_stage and guard_result.stage != expected_stage:
        result.fail(f"expected Guard stage {expected_stage}, got {guard_result.stage}")

    reason_contains = expected.get("reason_contains")
    if reason_contains and reason_contains not in (guard_result.reason or ""):
        result.fail(f"expected reason to contain {reason_contains!r}, got {guard_result.reason!r}")


def _validate_normal_case(
    result: SmokeResult,
    expected: dict[str, Any],
    matched_query_id: str | None,
    query_result,
    explainability: dict[str, Any],
    chart_type: str,
) -> None:
    if matched_query_id != expected.get("matched_query_id"):
        result.fail(f"expected matched_query_id {expected.get('matched_query_id')}, got {matched_query_id}")

    expected_columns = expected.get("result_columns") or []
    if query_result.columns != expected_columns:
        result.fail(f"expected result columns {expected_columns}, got {query_result.columns}")

    min_row_count = expected.get("min_row_count")
    if min_row_count is not None and query_result.row_count < min_row_count:
        result.fail(f"expected at least {min_row_count} rows, got {query_result.row_count}")

    expected_chart_type = expected.get("chart_type")
    if expected_chart_type and chart_type != expected_chart_type:
        result.fail(f"expected chart type {expected_chart_type}, got {chart_type}")

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
        result.fail(f"missing expected {label}: {missing}; actual={actual}")


def _format_join_path(path: dict[str, Any]) -> str:
    return (
        f"{path.get('source_table')}.{path.get('source_column')}"
        f" -> {path.get('target_table')}.{path.get('target_column')}"
    )


def _print_results(results: list[SmokeResult]) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        details = f"guard={result.guard_stage}"
        if result.row_count is not None:
            details += f" rows={result.row_count}"
        print(f"[{status}] {result.case_id} ({details})")
        for message in result.messages:
            print(f"  - {message}")

    passed = sum(1 for result in results if result.passed)
    print(f"\n{passed}/{len(results)} smoke cases passed.")


if __name__ == "__main__":
    sys.exit(main())
