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
from backend.app.agent.semantic_grounding import (
    ConceptExtractionRequest,
    GroundingCheckRequest,
    SemanticRefutationAuditor,
    analyze_sql_semantic_facts,
)
from backend.app.agent.state import AgentState
from backend.app.config import get_settings
from backend.app.connectors.registry import get_datasource_manager
from backend.app.core.deepseek_provider import DeepSeekProvider
from backend.app.execution.runner import execute_guarded_sql
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.metadata.service import build_focused_context_from_retrieval
from backend.app.sql_guard.scope import build_default_guard_scope


DEFAULT_MIN_COMPLETED = 20
DEFAULT_MIN_POSITIVE_FIXTURES = 1
DEFAULT_MIN_NEGATIVE_FIXTURES = 1
PROMOTED_PATTERNS_PATH = ROOT_DIR / "evals" / "promoted_patterns.json"


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
    case_type: str = "workflow"
    promotion_pattern: str | None = None
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
    inconclusive: bool = False

    def fail(self, message: str) -> None:
        self.passed = False
        self.messages.append(message)

    def mark_inconclusive(self, message: str) -> None:
        self.inconclusive = True
        self.messages.append(message)

    @property
    def status(self) -> str:
        if self.inconclusive:
            return "inconclusive"
        return "pass" if self.passed else "fail"


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
    parser.add_argument(
        "--promotion-pattern",
        action="append",
        default=None,
        help="Run cases for a specific promotion pattern. Can be passed more than once.",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Repeat each selected case N times.")
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help="Retry a failed case up to N times. Useful for transient provider availability during eval collection.",
    )
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Run only cases tagged smoke. Smoke cases are non-gating for promotion.",
    )
    parser.add_argument(
        "--write-promoted",
        action="store_true",
        help="Recompute and overwrite evals/promoted_patterns.json from this run.",
    )
    parser.add_argument("--min-completed", type=int, default=DEFAULT_MIN_COMPLETED)
    args = parser.parse_args()

    try:
        cases = _load_cases(Path(args.cases_path))
        if args.case_id:
            cases = _select_case_ids(cases, args.case_id)
        if args.promotion_pattern:
            cases = _filter_promotion_patterns(cases, args.promotion_pattern)
        if args.smoke_only:
            cases = [case for case in cases if "smoke" in (case.get("tags") or [])]
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
    readiness = evaluate_promotion_readiness(results, min_completed=args.min_completed)
    if args.write_promoted:
        promoted = write_promoted_patterns(readiness, path=PROMOTED_PATTERNS_PATH)
        print(f"promoted patterns written to {PROMOTED_PATTERNS_PATH}: {_format_list(promoted)}")
    report_path = Path(args.report_path)
    _write_report(report_path, results, semantic_mode=args.semantic_mode, readiness=readiness)
    _print_results(results, report_path=report_path)
    return 0 if all(result.passed for result in results if not result.inconclusive) else 1


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


def _filter_promotion_patterns(cases: list[dict[str, Any]], patterns: list[str]) -> list[dict[str, Any]]:
    selected_patterns = set(patterns)
    selected = [case for case in cases if case.get("promotion_pattern") in selected_patterns]
    missing = sorted(selected_patterns - {str(case.get("promotion_pattern")) for case in selected})
    if missing:
        raise ValueError(f"unknown semantic guard promotion patterns: {missing}")
    return selected


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
        case_type=str(case.get("type") or "workflow"),
        promotion_pattern=_optional_string(case.get("promotion_pattern")),
        datasource_name=datasource_name,
        datasource_dialect=datasource.dialect if datasource else "unknown",
        datasource_display_name=datasource.display_name if datasource else datasource_name,
    )
    started_at = time.perf_counter()
    try:
        if result.case_type == "verifier_only":
            _run_verifier_only_case(result, case, verifier=semantic_verifier)
            return result
        if result.case_type == "fixture":
            _run_fixture_case(result, case, verifier=semantic_verifier, auditor=auditor)
            return result
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


def _run_verifier_only_case(
    result: SemanticEvalResult,
    case: dict[str, Any],
    *,
    verifier: DeepSeekProvider,
) -> None:
    full_schema_context = case.get("full_schema_context")
    if not isinstance(full_schema_context, str) or not full_schema_context.strip():
        result.fail("verifier_only case requires full_schema_context")
        return
    try:
        extraction = verifier.extract_required_concepts(
            ConceptExtractionRequest(
                question=result.question,
                full_schema_context=full_schema_context,
                datasource_name=result.datasource_name,
                datasource_dialect=result.datasource_dialect,
            )
        )
    except Exception as exc:
        result.verifier_unavailable = True
        result.mark_inconclusive(f"semantic verifier unavailable: {exc}")
        return
    result.required_concepts = [concept.model_dump() for concept in extraction.concepts]
    _validate_expected_required_concepts(result, case.get("expected") or {})


def _run_fixture_case(
    result: SemanticEvalResult,
    case: dict[str, Any],
    *,
    verifier: DeepSeekProvider,
    auditor: SemanticRefutationAuditor,
) -> None:
    from backend.app.agent.semantic_grounding import (
        _concept_for_issue,
        _normalize_grounding_result,
        _warning_from_issue,
    )

    full_context = case.get("full_schema_context")
    sql = case.get("sql")
    if not isinstance(full_context, str) or not full_context.strip() or not isinstance(sql, str) or not sql.strip():
        result.fail("fixture case requires full_schema_context and sql")
        return

    try:
        extraction = verifier.extract_required_concepts(
            ConceptExtractionRequest(
                question=result.question,
                full_schema_context=full_context,
                datasource_name=result.datasource_name,
                datasource_dialect=result.datasource_dialect,
            )
        )
    except Exception as exc:
        result.verifier_unavailable = True
        result.mark_inconclusive(f"semantic verifier unavailable: {exc}")
        return

    unsupported = tuple(concept for concept in extraction.concepts if not concept.supported)
    result.required_concepts = [concept.model_dump() for concept in extraction.concepts]
    if not unsupported:
        result.semantic_ok = True
        result.warning_count = 0
        _validate_expected_fixture(result, case.get("expected") or {})
        return

    facts = analyze_sql_semantic_facts(sql, datasource_dialect=result.datasource_dialect)
    try:
        grounding = verifier.check_grounding(
            GroundingCheckRequest(
                question=result.question,
                sql=sql,
                concepts=unsupported,
                sql_facts=facts,
                datasource_name=result.datasource_name,
                datasource_dialect=result.datasource_dialect,
            )
        )
    except Exception as exc:
        result.verifier_unavailable = True
        result.mark_inconclusive(f"semantic verifier unavailable: {exc}")
        return

    normalized_grounding = _normalize_grounding_result(grounding, unsupported)
    evidence = auditor.evidence(datasource_name=result.datasource_name)
    for issue in normalized_grounding.issues:
        concept = _concept_for_issue(issue, unsupported)
        refutation = auditor.audit(issue, evidence=evidence, concept=concept)
        result.warnings.append(_warning_from_issue(issue, refutation))
    result.semantic_ok = normalized_grounding.ok
    result.warning_count = len(result.warnings)
    result.sql = sql
    _validate_expected_fixture(result, case.get("expected") or {})


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
        if result.passed and not result.inconclusive:
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
    if result.verifier_unavailable:
        result.mark_inconclusive("semantic verifier unavailable (inconclusive, not a semantic failure)")
        return
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


def _validate_expected_fixture(result: SemanticEvalResult, expected: dict[str, Any]) -> None:
    expected_warning = expected.get("warning")
    result.expected_warning = bool(expected_warning) if expected_warning is not None else None
    if expected_warning is not None and bool(result.warnings) != bool(expected_warning):
        result.fail(f"expected warning={expected_warning}, got {bool(result.warnings)}")
    if "refutation_confirmed" in expected:
        actual = bool(result.warnings) and all(warning.get("refutation_confirmed") for warning in result.warnings)
        if actual != bool(expected["refutation_confirmed"]):
            result.fail(f"expected refutation_confirmed={expected['refutation_confirmed']}, got {actual}")
    expected_pattern = expected.get("refutation_pattern")
    if expected_pattern is not None:
        actual_patterns = {
            warning.get("refutation_pattern")
            for warning in result.warnings
            if warning.get("refutation_confirmed")
        }
        if actual_patterns != {expected_pattern}:
            result.fail(
                f"expected refutation_pattern={expected_pattern!r}, "
                f"got {sorted(pattern for pattern in actual_patterns if pattern)}"
            )


def _validate_expected_required_concepts(result: SemanticEvalResult, expected: dict[str, Any]) -> None:
    for expectation in expected.get("required_concepts") or []:
        if not isinstance(expectation, dict):
            continue
        expected_name = str(expectation.get("concept") or expectation.get("name") or "").strip()
        if not expected_name:
            continue
        actual = _find_required_concept(result.required_concepts, expected_name)
        if actual is None:
            result.fail(
                f"missing required concept {expected_name!r}; "
                f"actual={[concept.get('concept') for concept in result.required_concepts]}"
            )
            continue

        expected_supported = expectation.get("supported")
        if expected_supported is not None and bool(actual.get("supported")) != bool(expected_supported):
            result.fail(
                f"expected concept {expected_name!r} supported={expected_supported}, "
                f"got {actual.get('supported')} ({actual.get('explanation') or '-'})"
            )

        expected_type = expectation.get("concept_type")
        if expected_type and actual.get("concept_type") != expected_type:
            result.fail(
                f"expected concept {expected_name!r} type={expected_type}, "
                f"got {actual.get('concept_type')}"
            )


def _find_required_concept(concepts: list[dict[str, Any]], expected_name: str) -> dict[str, Any] | None:
    normalized_expected = _normalize_concept_name(expected_name)
    for concept in concepts:
        actual_name = str(concept.get("concept") or "")
        normalized_actual = _normalize_concept_name(actual_name)
        if (
            normalized_expected == normalized_actual
            or normalized_expected in normalized_actual
            or normalized_actual in normalized_expected
        ):
            return concept
    return None


def _normalize_concept_name(value: str) -> str:
    return "".join(value.casefold().split())


def _print_results(results: list[SemanticEvalResult], *, report_path: Path) -> None:
    for result in results:
        status = result.status.upper()
        print(
            f"[{status}] {result.case_id} "
            f"type={result.case_type} "
            f"pattern={result.promotion_pattern or '-'} "
            f"warnings={result.warning_count} expected={result.expected_warning} "
            f"rows={result.row_count if result.row_count is not None else '-'} "
            f"elapsed={_format_elapsed(result.elapsed_ms)}"
        )
        for message in result.messages:
            print(f"  - {message}")
    summary = _summary(results)
    print(
        "\n"
        f"{summary['passed_cases']}/{summary['completed_cases']} completed semantic guard cases passed; "
        f"inconclusive={summary['inconclusive_cases']}; "
        f"warnings={summary['warning_cases']}; "
        f"confirmed={summary['confirmed_warning_cases']}; "
        f"verifier_unavailable={summary['verifier_unavailable_cases']}."
    )
    print(f"report: {report_path}")


def _write_report(
    path: Path,
    results: list[SemanticEvalResult],
    *,
    semantic_mode: str,
    readiness: dict[str, dict[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_report(results, semantic_mode=semantic_mode, readiness=readiness), encoding="utf-8")


def _render_report(
    results: list[SemanticEvalResult],
    *,
    semantic_mode: str,
    readiness: dict[str, dict[str, Any]] | None = None,
) -> str:
    summary = _summary(results)
    availability = availability_report(results)
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
    promotion_stats = _promotion_pattern_stats(results)
    lines = [
        "# Semantic Guard Eval Report",
        "",
        "## Summary",
        "",
        f"- Semantic mode: {semantic_mode}",
        f"- Cases: {summary['total_cases']}",
        f"- Completed: {summary['completed_cases']}",
        f"- Passed: {summary['passed_cases']}/{summary['completed_cases']}",
        f"- Inconclusive: {summary['inconclusive_cases']}",
        f"- Verifier-only cases: {summary['verifier_only_cases']}",
        f"- Fixture cases: {summary['fixture_cases']}",
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
        "## Availability (SLO — non-gating)",
        "",
        f"- Completed observations: {availability['completed_observations']}",
        f"- Inconclusive observations: {availability['inconclusive_observations']}",
        f"- Availability rate: {availability['availability_rate']}",
        f"- Chronically unavailable case ids: {_format_list(availability['chronically_unavailable_case_ids'])}",
        "",
        "## Promotion Pattern Readiness",
        "",
    ]
    if readiness:
        lines.extend(
            [
                "| Pattern | Promotable | Reason | Completed | Failed | Inconclusive | False Confirmed | Positive Fixtures | Positive Matched | Negative Fixtures |",
                "|---------|------------|--------|-----------|--------|--------------|-----------------|-------------------|------------------|-------------------|",
            ]
        )
        for pattern, info in readiness.items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(pattern),
                        str(info["promotable"]),
                        _md_cell(info["reason"]),
                        str(info["completed"]),
                        str(info["failed_completed"]),
                        str(info["inconclusive"]),
                        str(info["false_confirmed"]),
                        str(info["positive_fixtures"]),
                        str(info["positive_fixtures_matched"]),
                        str(info["negative_fixtures"]),
                    ]
                )
                + " |"
            )
    else:
        lines.append("No promotable patterns in this selection.")

    lines.extend(
        [
            "",
            "## Promotion Pattern Observations",
            "",
            "| Pattern | Cases | Passed | Expected Warnings | Actual Warnings | Confirmed Warnings | False Confirmed Warnings | Verifier-only Positive | Verifier-only Negative | Verifier Unavailable |",
            "|---------|-------|--------|-------------------|-----------------|--------------------|--------------------------|------------------------|------------------------|----------------------|",
        ]
    )
    if promotion_stats:
        for pattern, stats in promotion_stats.items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(pattern),
                        str(stats["cases"]),
                        f"{stats['passed']}/{stats['cases']}",
                        str(stats["expected_warning_cases"]),
                        str(stats["actual_warning_cases"]),
                        str(stats["confirmed_warning_cases"]),
                        str(stats["false_confirmed_warning_cases"]),
                        f"{stats['verifier_positive_passed']}/{stats['verifier_positive_cases']}",
                        f"{stats['verifier_negative_passed']}/{stats['verifier_negative_cases']}",
                        str(stats["verifier_unavailable_cases"]),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| n/a | 0 | 0/0 | 0 | 0 | 0 | 0 | 0/0 | 0/0 | 0 |")

    lines.append("")
    lines.extend(
        [
            "## Case Results",
            "",
            "| Case | Status | Type | Pattern | Expected Warning | Warnings | Confirmed | Rows | Elapsed | Required Concepts | Warning Concepts | Kinds | SQL |",
            "|------|--------|------|---------|------------------|----------|-----------|------|---------|-------------------|------------------|-------|-----|",
        ]
    )
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(result.case_id),
                    result.status.upper(),
                    _md_cell(result.case_type),
                    _md_cell(result.promotion_pattern or "-"),
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

    failures = [result for result in results if not result.passed and not result.inconclusive]
    lines.extend(["", "## Failure Details", ""])
    if not failures:
        lines.append("No failures.")
    else:
        for result in failures:
            lines.extend([f"### {result.case_id}", ""])
            lines.extend(
                [
                    f"- Question: {result.question}",
                    f"- Type: {result.case_type}",
                    f"- Promotion pattern: {result.promotion_pattern or '-'}",
                    f"- Expected warning: {result.expected_warning}",
                    f"- Warning count: {result.warning_count}",
                    f"- Verifier unavailable: {result.verifier_unavailable}",
                    f"- Required concepts: {_format_required_concepts(result.required_concepts)}",
                ]
            )
            if result.required_concepts:
                lines.extend(["", "Required concept details:", "", "```json"])
                lines.append(json.dumps(result.required_concepts, ensure_ascii=False, indent=2))
                lines.append("```")
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
    completed = [result for result in results if not result.inconclusive]
    return {
        "total_cases": len(results),
        "completed_cases": len(completed),
        "inconclusive_cases": sum(1 for result in results if result.inconclusive),
        "passed_cases": sum(1 for result in completed if result.passed),
        "verifier_only_cases": sum(1 for result in results if result.case_type == "verifier_only"),
        "fixture_cases": sum(1 for result in results if result.case_type == "fixture"),
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


def availability_report(results: list[SemanticEvalResult]) -> dict[str, Any]:
    completed = sum(1 for result in results if not result.inconclusive)
    inconclusive = sum(1 for result in results if result.inconclusive)
    by_case: dict[str, list[SemanticEvalResult]] = {}
    for result in results:
        case_id = result.case_id.split("__run", 1)[0]
        by_case.setdefault(case_id, []).append(result)
    chronic = sorted(
        case_id
        for case_id, observations in by_case.items()
        if observations and all(observation.inconclusive for observation in observations)
    )
    total = completed + inconclusive
    return {
        "completed_observations": completed,
        "inconclusive_observations": inconclusive,
        "availability_rate": round(completed / total, 4) if total else None,
        "chronically_unavailable_case_ids": chronic,
    }


def evaluate_promotion_readiness(
    results: list[SemanticEvalResult],
    *,
    min_completed: int = DEFAULT_MIN_COMPLETED,
    min_positive: int = DEFAULT_MIN_POSITIVE_FIXTURES,
    min_negative: int = DEFAULT_MIN_NEGATIVE_FIXTURES,
) -> dict[str, dict[str, Any]]:
    by_pattern: dict[str, list[SemanticEvalResult]] = {}
    for result in results:
        if result.promotion_pattern and "smoke" not in result.tags:
            by_pattern.setdefault(result.promotion_pattern, []).append(result)

    readiness: dict[str, dict[str, Any]] = {}
    for pattern, pattern_results in by_pattern.items():
        completed = [result for result in pattern_results if not result.inconclusive]
        failed_completed = [result for result in completed if not result.passed]
        false_confirmed = [
            result
            for result in completed
            if result.expected_warning is False
            and any(warning.get("refutation_confirmed") for warning in result.warnings)
        ]
        positive = [result for result in completed if _is_verifier_positive_case(result)]
        positive_valid = [result for result in positive if _confirmed_under_pattern(result, pattern)]
        negative = [result for result in completed if _is_verifier_negative_case(result)]

        if len(completed) < min_completed:
            promotable, reason = False, f"insufficient completed observations ({len(completed)}/{min_completed})"
        elif false_confirmed:
            promotable, reason = False, f"false_confirmed refutation on {len(false_confirmed)} case(s)"
        elif failed_completed:
            promotable, reason = False, f"{len(failed_completed)} completed case(s) failed"
        elif len(positive_valid) < min_positive or len(negative) < min_negative:
            promotable, reason = False, (
                f"insufficient pattern-matched fixture coverage "
                f"(+{len(positive_valid)}/{min_positive}, -{len(negative)}/{min_negative})"
            )
        else:
            promotable, reason = True, "all completed checks passed"

        readiness[pattern] = {
            "promotable": promotable,
            "reason": reason,
            "completed": len(completed),
            "failed_completed": len(failed_completed),
            "inconclusive": len(pattern_results) - len(completed),
            "false_confirmed": len(false_confirmed),
            "positive_fixtures": len(positive),
            "positive_fixtures_matched": len(positive_valid),
            "negative_fixtures": len(negative),
        }
    return readiness


def _confirmed_under_pattern(result: SemanticEvalResult, pattern: str) -> bool:
    return any(
        warning.get("refutation_confirmed") and warning.get("refutation_pattern") == pattern
        for warning in result.warnings
    )


def write_promoted_patterns(readiness: dict[str, dict[str, Any]], *, path: Path) -> list[str]:
    promoted = sorted(pattern for pattern, info in readiness.items() if info["promotable"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"promoted": promoted}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return promoted


def _promotion_pattern_stats(results: list[SemanticEvalResult]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for result in results:
        if not result.promotion_pattern:
            continue
        pattern_stats = stats.setdefault(
            result.promotion_pattern,
            {
                "cases": 0,
                "passed": 0,
                "expected_warning_cases": 0,
                "actual_warning_cases": 0,
                "confirmed_warning_cases": 0,
                "false_confirmed_warning_cases": 0,
                "verifier_positive_cases": 0,
                "verifier_positive_passed": 0,
                "verifier_negative_cases": 0,
                "verifier_negative_passed": 0,
                "verifier_unavailable_cases": 0,
            },
        )
        pattern_stats["cases"] += 1
        pattern_stats["passed"] += int(result.passed)
        pattern_stats["expected_warning_cases"] += int(result.expected_warning is True)
        pattern_stats["actual_warning_cases"] += int(result.warning_count > 0)
        pattern_stats["confirmed_warning_cases"] += int(
            bool(result.warnings) and all(warning.get("refutation_confirmed") for warning in result.warnings)
        )
        pattern_stats["false_confirmed_warning_cases"] += int(
            result.expected_warning is False and any(warning.get("refutation_confirmed") for warning in result.warnings)
        )
        if _is_verifier_positive_case(result):
            pattern_stats["verifier_positive_cases"] += 1
            pattern_stats["verifier_positive_passed"] += int(result.passed)
        if _is_verifier_negative_case(result):
            pattern_stats["verifier_negative_cases"] += 1
            pattern_stats["verifier_negative_passed"] += int(result.passed)
        pattern_stats["verifier_unavailable_cases"] += int(result.verifier_unavailable)
    return stats


def _is_verifier_positive_case(result: SemanticEvalResult) -> bool:
    return result.case_type in {"verifier_only", "fixture"} and "positive_schema" in result.tags


def _is_verifier_negative_case(result: SemanticEvalResult) -> bool:
    return result.case_type in {"verifier_only", "fixture"} and "negative_schema" in result.tags


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


def _format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "-"


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _md_cell(value: object) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


if __name__ == "__main__":
    raise SystemExit(main())
