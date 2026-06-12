from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from backend.app.agent.nodes import generate_sql_node, olap_intent_detect_node
from backend.app.agent.repair import iter_sql_repair_events
from backend.app.agent.semantic_grounding import SemanticRefutationAuditor
from backend.app.agent.state import AgentState
from backend.app.config import get_settings
from backend.app.connectors.registry import get_datasource_manager
from backend.app.core.deepseek_provider import DeepSeekProvider
from backend.app.execution.runner import execute_guarded_sql
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.metadata.service import build_focused_context_from_retrieval
from backend.app.sql_guard.scope import build_default_guard_scope


@dataclass(frozen=True)
class DatasourceRef:
    name: str
    dialect: str
    display_name: str


@dataclass
class SemanticEvalResult:
    case_id: str
    question: str
    tags: list[str]
    datasource_name: str = DEFAULT_DATASOURCE
    datasource_dialect: str = "duckdb"
    datasource_display_name: str = "DuckDB (本地)"
    passed: bool = True
    messages: list[str] = field(default_factory=list)
    sql: str | None = None
    row_count: int | None = None
    warning_count: int = 0
    expected_warning: bool | None = None
    verifier_unavailable: bool = False
    semantic_ok: bool | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    required_concepts: list[dict[str, Any]] = field(default_factory=list)
    semantic_guard_result: dict[str, Any] | None = None
    error: str | None = None
    elapsed_ms: int | None = None

    def fail(self, message: str) -> None:
        self.passed = False
        self.messages.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 1 semantic grounding guard eval cases.")
    parser.add_argument(
        "cases_path",
        nargs="?",
        default="evals/semantic_guard_cases.yaml",
        help="Path to semantic guard eval case YAML file.",
    )
    parser.add_argument(
        "--report-path",
        default="evals/reports/semantic_guard_latest.md",
        help="Path for the Markdown semantic guard eval report.",
    )
    parser.add_argument(
        "--provider",
        choices=("deepseek",),
        default="deepseek",
        help="LLM provider to evaluate. Semantic guard Phase 1 requires a real verifier.",
    )
    parser.add_argument(
        "--semantic-mode",
        choices=("warn", "enforce"),
        default="warn",
        help="Semantic guard mode for the run. Phase 1 evals should use warn.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N selected cases.")
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="Run a specific case id. Can be passed more than once.",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Repeat each selected case N times.")
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help="Retry a failed case up to N times. Useful for transient provider availability during eval collection.",
    )
    args = parser.parse_args()

    try:
        cases = _load_cases(Path(args.cases_path))
        if args.case_id:
            cases = _select_case_ids(cases, args.case_id)
        if args.limit is not None:
            cases = cases[: args.limit]
        cases = _repeat_cases(cases, repeat=args.repeat)
        datasources = _available_datasources()
        settings = get_settings()
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for semantic guard eval.")
        generation_provider = DeepSeekProvider()
        semantic_verifier = DeepSeekProvider(timeout=settings.semantic_guard_timeout)
        auditor = SemanticRefutationAuditor()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results = [
        _run_case_with_retries(
            case,
            datasources=datasources,
            generation_provider=generation_provider,
            semantic_verifier=semantic_verifier,
            auditor=auditor,
            semantic_mode=args.semantic_mode,
            retries=args.retries,
        )
        for case in cases
    ]
    report_path = Path(args.report_path)
    _write_report(report_path, results, semantic_mode=args.semantic_mode)
    _print_results(results, report_path=report_path)
    return 0 if all(result.passed for result in results) else 1


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", []) if isinstance(payload, dict) else []
    if not isinstance(cases, list):
        raise ValueError("semantic guard case file must contain a list under 'cases'.")
    return cases


def _repeat_cases(cases: list[dict[str, Any]], *, repeat: int) -> list[dict[str, Any]]:
    if repeat <= 1:
        return cases
    repeated = []
    for case in cases:
        for iteration in range(1, repeat + 1):
            repeated_case = dict(case)
            repeated_case["id"] = f"{case['id']}__run{iteration}"
            repeated.append(repeated_case)
    return repeated


def _select_case_ids(cases: list[dict[str, Any]], case_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {str(case.get("id")): case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown semantic guard case ids: {missing}")
    return [by_id[case_id] for case_id in case_ids]


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


def _run_case(
    case: dict[str, Any],
    *,
    datasources: dict[str, DatasourceRef],
    generation_provider: DeepSeekProvider,
    semantic_verifier: DeepSeekProvider,
    auditor: SemanticRefutationAuditor,
    semantic_mode: str,
) -> SemanticEvalResult:
    datasource_name = str(case.get("datasource") or DEFAULT_DATASOURCE)
    datasource = datasources.get(datasource_name)
    result = SemanticEvalResult(
        case_id=str(case["id"]),
        question=str(case["question"]),
        tags=list(case.get("tags") or []),
        datasource_name=datasource_name,
        datasource_dialect=datasource.dialect if datasource else "unknown",
        datasource_display_name=datasource.display_name if datasource else datasource_name,
    )
    started_at = time.perf_counter()
    try:
        if datasource is None:
            result.fail(f"datasource unavailable: {datasource_name}")
            return result

        state = AgentState(
            question=result.question,
            datasource_name=datasource.name,
            datasource_dialect=datasource.dialect,
            datasource_display_name=datasource.display_name,
        )
        state.retrieval_result = retrieve_metadata_assets(result.question, datasource_name=datasource.name)
        state.schema_context = build_focused_context_from_retrieval(
            state.retrieval_result,
            datasource_name=datasource.name,
        )
        olap_intent_detect_node(state)
        try:
            generate_sql_node(state, provider=generation_provider)
        except httpx.ReadTimeout as exc:
            result.fail(f"SQL generation timed out: {exc}")
            return result
        except Exception as exc:
            result.fail(f"SQL generation failed: {exc}")
            return result

        scope = build_default_guard_scope(datasource_name=datasource.name)
        try:
            repair_events = list(
                iter_sql_repair_events(
                    state,
                    provider=generation_provider,
                    scope_builder=lambda datasource_name=datasource.name: scope,
                    executor=execute_guarded_sql,
                    semantic_verifier=semantic_verifier,
                    semantic_auditor=auditor,
                    semantic_mode=semantic_mode,
                )
            )
        except Exception as exc:
            result.fail(f"workflow failed: {exc}")
            return result

        final_event = repair_events[-1] if repair_events else None
        if final_event and final_event.step == "error":
            result.error = final_event.error_reason
            result.fail(f"workflow ended with {final_event.error_stage}: {final_event.error_reason}")

        _record_state(result, state)
        _validate_expected_semantic(result, case.get("expected") or {})
        return result
    finally:
        result.elapsed_ms = round((time.perf_counter() - started_at) * 1000)


def _run_case_with_retries(
    case: dict[str, Any],
    *,
    datasources: dict[str, DatasourceRef],
    generation_provider: DeepSeekProvider,
    semantic_verifier: DeepSeekProvider,
    auditor: SemanticRefutationAuditor,
    semantic_mode: str,
    retries: int,
) -> SemanticEvalResult:
    attempts = max(retries, 0) + 1
    last_result: SemanticEvalResult | None = None
    for attempt in range(1, attempts + 1):
        result = _run_case(
            case,
            datasources=datasources,
            generation_provider=generation_provider,
            semantic_verifier=semantic_verifier,
            auditor=auditor,
            semantic_mode=semantic_mode,
        )
        if result.passed:
            if attempt > 1:
                result.messages.append(f"passed after retry attempt {attempt}/{attempts}")
            return result
        last_result = result
    if last_result is None:
        raise RuntimeError("semantic eval retry loop produced no result")
    if attempts > 1:
        last_result.messages.append(f"failed after {attempts} attempts")
    return last_result


def _record_state(result: SemanticEvalResult, state: AgentState) -> None:
    result.sql = state.sql
    result.row_count = state.query_result.row_count if state.query_result is not None else None
    result.warnings = list(state.grounding_warnings)
    result.warning_count = len(result.warnings)
    result.required_concepts = list(state.required_concepts or [])
    result.semantic_guard_result = state.semantic_guard_result
    if state.semantic_guard_result is not None:
        result.semantic_ok = state.semantic_guard_result.get("ok")
        result.verifier_unavailable = bool(state.semantic_guard_result.get("verifier_unavailable"))


def _validate_expected_semantic(result: SemanticEvalResult, expected: dict[str, Any]) -> None:
    expected_warning = expected.get("warning")
    result.expected_warning = bool(expected_warning) if expected_warning is not None else None
    if result.verifier_unavailable and not expected.get("allow_verifier_unavailable", False):
        result.fail("semantic verifier unavailable")
    if expected_warning is not None and bool(result.warnings) != bool(expected_warning):
        result.fail(f"expected warning={expected_warning}, got {bool(result.warnings)}")

    expected_concepts = list(expected.get("concepts") or [])
    if expected_concepts:
        actual_concepts = [str(warning.get("concept") or "") for warning in result.warnings]
        missing = [
            concept
            for concept in expected_concepts
            if not any(concept in actual for actual in actual_concepts)
        ]
        if missing:
            result.fail(f"missing warning concepts {missing}; actual={actual_concepts}")

    expected_kinds = list(expected.get("failure_kinds") or [])
    if expected_kinds:
        actual_kinds = [str(warning.get("failure_kind") or "") for warning in result.warnings]
        missing = sorted(set(expected_kinds) - set(actual_kinds))
        if missing:
            result.fail(f"missing warning failure kinds {missing}; actual={actual_kinds}")

    expected_confirmed = expected.get("refutation_confirmed")
    if expected_confirmed is not None and result.warnings:
        mismatched = [
            warning
            for warning in result.warnings
            if bool(warning.get("refutation_confirmed")) != bool(expected_confirmed)
        ]
        if mismatched:
            result.fail(
                "expected all warnings refutation_confirmed="
                f"{expected_confirmed}, got {[warning.get('refutation_confirmed') for warning in result.warnings]}"
            )


def _print_results(results: list[SemanticEvalResult], *, report_path: Path) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(
            f"[{status}] {result.case_id} "
            f"warnings={result.warning_count} expected={result.expected_warning} "
            f"rows={result.row_count if result.row_count is not None else '-'} "
            f"elapsed={_format_elapsed(result.elapsed_ms)}"
        )
        for message in result.messages:
            print(f"  - {message}")
    summary = _summary(results)
    print(
        "\n"
        f"{summary['passed_cases']}/{summary['total_cases']} semantic guard cases passed; "
        f"warnings={summary['warning_cases']}; "
        f"confirmed={summary['confirmed_warning_cases']}; "
        f"verifier_unavailable={summary['verifier_unavailable_cases']}."
    )
    print(f"report: {report_path}")


def _write_report(path: Path, results: list[SemanticEvalResult], *, semantic_mode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_report(results, semantic_mode=semantic_mode), encoding="utf-8")


def _render_report(results: list[SemanticEvalResult], *, semantic_mode: str) -> str:
    summary = _summary(results)
    warning_kinds = Counter(
        str(warning.get("failure_kind") or "-")
        for result in results
        for warning in result.warnings
    )
    warning_concepts = Counter(
        str(warning.get("concept") or "-")
        for result in results
        for warning in result.warnings
    )
    lines = [
        "# Semantic Guard Eval Report",
        "",
        "## Summary",
        "",
        f"- Semantic mode: {semantic_mode}",
        f"- Cases: {summary['total_cases']}",
        f"- Passed: {summary['passed_cases']}/{summary['total_cases']}",
        f"- Warning cases: {summary['warning_cases']}",
        f"- Expected-warning cases: {summary['expected_warning_cases']}",
        f"- Confirmed warning cases: {summary['confirmed_warning_cases']}",
        f"- Verifier unavailable: {summary['verifier_unavailable_cases']}",
        f"- Avg elapsed: {_format_elapsed(summary['avg_elapsed_ms'])}",
        "",
        "## Warning Distribution",
        "",
        f"- Failure kinds: {_format_distribution(warning_kinds)}",
        f"- Concepts: {_format_distribution(warning_concepts)}",
        "",
        "## Case Results",
        "",
        "| Case | Status | Expected Warning | Warnings | Confirmed | Rows | Elapsed | Required Concepts | Warning Concepts | Kinds | SQL |",
        "|------|--------|------------------|----------|-----------|------|---------|-------------------|------------------|-------|-----|",
    ]
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(result.case_id),
                    "PASS" if result.passed else "FAIL",
                    str(result.expected_warning),
                    str(result.warning_count),
                    str(sum(1 for warning in result.warnings if warning.get("refutation_confirmed"))),
                    str(result.row_count) if result.row_count is not None else "-",
                    _format_elapsed(result.elapsed_ms),
                    _md_cell(_format_required_concepts(result.required_concepts)),
                    _md_cell(", ".join(str(warning.get("concept") or "-") for warning in result.warnings) or "-"),
                    _md_cell(", ".join(str(warning.get("failure_kind") or "-") for warning in result.warnings) or "-"),
                    _md_cell(_short_sql(result.sql) or "-"),
                ]
            )
            + " |"
        )

    failures = [result for result in results if not result.passed]
    lines.extend(["", "## Failure Details", ""])
    if not failures:
        lines.append("No failures.")
    else:
        for result in failures:
            lines.extend([f"### {result.case_id}", ""])
            lines.extend(
                [
                    f"- Question: {result.question}",
                    f"- Expected warning: {result.expected_warning}",
                    f"- Warning count: {result.warning_count}",
                    f"- Verifier unavailable: {result.verifier_unavailable}",
                    f"- Required concepts: {_format_required_concepts(result.required_concepts)}",
                ]
            )
            for message in result.messages:
                lines.append(f"- {message}")
            if result.semantic_guard_result is not None:
                lines.extend(["", "Semantic guard result:", "", "```json"])
                lines.append(json.dumps(result.semantic_guard_result, ensure_ascii=False, indent=2))
                lines.append("```")
            if result.sql:
                lines.extend(["", "SQL:", "", "```sql", result.sql, "```"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _summary(results: list[SemanticEvalResult]) -> dict[str, Any]:
    elapsed_values = [result.elapsed_ms for result in results if result.elapsed_ms is not None]
    return {
        "total_cases": len(results),
        "passed_cases": sum(1 for result in results if result.passed),
        "warning_cases": sum(1 for result in results if result.warning_count > 0),
        "expected_warning_cases": sum(1 for result in results if result.expected_warning is True),
        "confirmed_warning_cases": sum(
            1
            for result in results
            if result.warnings and all(warning.get("refutation_confirmed") for warning in result.warnings)
        ),
        "verifier_unavailable_cases": sum(1 for result in results if result.verifier_unavailable),
        "avg_elapsed_ms": round(sum(elapsed_values) / len(elapsed_values)) if elapsed_values else None,
    }


def _format_distribution(counter: Counter[str]) -> str:
    if not counter:
        return "-"
    return ", ".join(f"{key}={count}" for key, count in counter.most_common())


def _format_elapsed(elapsed_ms: int | None) -> str:
    if elapsed_ms is None:
        return "-"
    if elapsed_ms < 1000:
        return f"{elapsed_ms}ms"
    return f"{elapsed_ms / 1000:.1f}s"


def _short_sql(sql: str | None, *, max_length: int = 140) -> str:
    if not sql:
        return ""
    compact = " ".join(sql.split())
    return compact if len(compact) <= max_length else compact[: max_length - 1] + "..."


def _format_required_concepts(concepts: list[dict[str, Any]]) -> str:
    if not concepts:
        return "-"
    formatted = []
    for concept in concepts:
        name = str(concept.get("concept") or "-")
        concept_type = str(concept.get("concept_type") or "other")
        supported = bool(concept.get("supported"))
        formatted.append(f"{name} ({concept_type}, supported={supported})")
    return ", ".join(formatted)


def _md_cell(value: object) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


if __name__ == "__main__":
    raise SystemExit(main())
