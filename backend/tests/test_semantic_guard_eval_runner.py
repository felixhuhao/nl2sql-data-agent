import importlib.util
import sys
from pathlib import Path

import pytest


RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_semantic_guard_eval.py"
SPEC = importlib.util.spec_from_file_location("run_semantic_guard_eval", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _pattern_case(
    case_id,
    *,
    pattern="p",
    passed=True,
    inconclusive=False,
    expected_warning=None,
    warnings=None,
    case_type="workflow",
    tags=None,
):
    result = runner.SemanticEvalResult(
        case_id=case_id,
        question="q",
        tags=tags or [],
        case_type=case_type,
        promotion_pattern=pattern,
    )
    result.expected_warning = expected_warning
    result.warnings = warnings or []
    result.warning_count = len(result.warnings)
    result.passed = passed
    result.inconclusive = inconclusive
    return result


def test_verifier_unavailable_is_inconclusive_not_fail():
    result = runner.SemanticEvalResult(case_id="x", question="q", tags=[])
    result.verifier_unavailable = True

    runner._validate_expected_semantic(result, {"warning": True})

    assert result.status == "inconclusive"
    assert result.passed is True
    assert result.inconclusive is True


def test_semantic_mismatch_is_fail_even_when_completed():
    result = runner.SemanticEvalResult(case_id="x", question="q", tags=[], warnings=[])

    runner._validate_expected_semantic(result, {"warning": True})

    assert result.status == "fail"
    assert result.inconclusive is False


def test_summary_separates_completed_from_inconclusive():
    completed_pass = runner.SemanticEvalResult(case_id="a", question="q", tags=[])
    completed_fail = runner.SemanticEvalResult(case_id="b", question="q", tags=[])
    completed_fail.fail("mismatch")
    inconclusive = runner.SemanticEvalResult(case_id="c", question="q", tags=[])
    inconclusive.mark_inconclusive("verifier unavailable")

    summary = runner._summary([completed_pass, completed_fail, inconclusive])

    assert summary["completed_cases"] == 2
    assert summary["inconclusive_cases"] == 1
    assert summary["passed_cases"] == 1


def test_retries_continue_past_inconclusive_until_completed(monkeypatch):
    attempts = []

    def fake_run_case(case, **kwargs):
        result = runner.SemanticEvalResult(case_id=case["id"], question="q", tags=[])
        if not attempts:
            result.mark_inconclusive("verifier unavailable")
        attempts.append(1)
        return result

    monkeypatch.setattr(runner, "_run_case", fake_run_case)

    result = runner._run_case_with_retries(
        {"id": "x"},
        datasources={},
        generation_provider=None,
        semantic_verifier=None,
        auditor=None,
        semantic_mode="warn",
        retries=1,
    )

    assert len(attempts) == 2
    assert result.status == "pass"


def test_validate_expected_semantic_passes_matching_warning():
    result = runner.SemanticEvalResult(
        case_id="unsupported",
        question="查看删除率趋势",
        tags=["unsupported"],
        warnings=[
            {
                "concept": "删除率",
                "failure_kind": "substituted",
                "refutation_confirmed": True,
            }
        ],
        warning_count=1,
    )

    runner._validate_expected_semantic(
        result,
        {
            "warning": True,
            "concepts": ["删除率"],
            "failure_kinds": ["substituted"],
            "refutation_confirmed": True,
        },
    )

    assert result.passed is True
    assert result.expected_warning is True


def test_select_case_ids_preserves_requested_order_and_rejects_unknown():
    cases = [{"id": "a"}, {"id": "b"}]

    assert runner._select_case_ids(cases, ["b", "a"]) == [{"id": "b"}, {"id": "a"}]
    with pytest.raises(ValueError, match="unknown semantic guard case ids"):
        runner._select_case_ids(cases, ["missing"])


def test_filter_promotion_patterns_selects_matching_cases():
    cases = [
        {"id": "a", "promotion_pattern": "concept_absent_full_metadata"},
        {"id": "b", "promotion_pattern": "other"},
        {"id": "c"},
    ]

    assert runner._filter_promotion_patterns(cases, ["concept_absent_full_metadata"]) == [cases[0]]
    with pytest.raises(ValueError, match="unknown semantic guard promotion patterns"):
        runner._filter_promotion_patterns(cases, ["missing"])


def test_run_case_with_retries_returns_later_pass(monkeypatch):
    attempts = []

    def fake_run_case(case, **kwargs):
        attempts.append(case["id"])
        result = runner.SemanticEvalResult(case_id=case["id"], question="q", tags=[])
        if len(attempts) == 1:
            result.fail("transient")
        return result

    monkeypatch.setattr(runner, "_run_case", fake_run_case)

    result = runner._run_case_with_retries(
        {"id": "case"},
        datasources={},
        generation_provider=None,
        semantic_verifier=None,
        auditor=None,
        semantic_mode="warn",
        retries=1,
    )

    assert result.passed is True
    assert attempts == ["case", "case"]
    assert result.messages == ["passed after retry attempt 2/2"]


def test_validate_expected_semantic_flags_false_positive_warning():
    result = runner.SemanticEvalResult(
        case_id="supported",
        question="查看退款率趋势",
        tags=["supported"],
        warnings=[{"concept": "退款率", "failure_kind": "substituted"}],
        warning_count=1,
    )

    runner._validate_expected_semantic(result, {"warning": False})

    assert result.passed is False
    assert "expected warning=False" in result.messages[0]


def test_render_report_includes_warning_distribution():
    result = runner.SemanticEvalResult(
        case_id="unsupported",
        question="查看删除率趋势",
        tags=["unsupported"],
        promotion_pattern="concept_absent_full_metadata",
        passed=True,
        warning_count=1,
        expected_warning=True,
        warnings=[
            {
                "concept": "删除率",
                "failure_kind": "substituted",
                "refutation_confirmed": True,
            }
        ],
    )

    report = runner._render_report([result], semantic_mode="warn")

    assert "# Semantic Guard Eval Report" in report
    assert "- Verifier-only cases: 0" in report
    assert "- Completed: 1" in report
    assert "concept_absent_full_metadata" in report
    assert "substituted=1" in report
    assert "删除率=1" in report


def test_format_required_concepts_includes_support_status():
    assert runner._format_required_concepts(
        [
            {
                "concept": "退货率",
                "concept_type": "metric",
                "supported": False,
            }
        ]
    ) == "退货率 (metric, supported=False)"


def test_validate_expected_required_concepts_checks_support_status():
    result = runner.SemanticEvalResult(
        case_id="verifier",
        question="查看退货率趋势",
        tags=["verifier_only"],
        required_concepts=[
            {
                "concept": "退货率",
                "concept_type": "metric",
                "supported": False,
                "explanation": "Only refund is documented.",
            }
        ],
    )

    runner._validate_expected_required_concepts(
        result,
        {
            "required_concepts": [
                {
                    "concept": "退货率",
                    "concept_type": "metric",
                    "supported": False,
                }
            ]
        },
    )

    assert result.passed is True


def test_validate_expected_required_concepts_flags_wrong_support_status():
    result = runner.SemanticEvalResult(
        case_id="verifier",
        question="查看退货率趋势",
        tags=["verifier_only"],
        required_concepts=[
            {
                "concept": "退货率",
                "concept_type": "metric",
                "supported": True,
                "explanation": "Refund was treated as return.",
            }
        ],
    )

    runner._validate_expected_required_concepts(
        result,
        {"required_concepts": [{"concept": "退货率", "supported": False}]},
    )

    assert result.passed is False
    assert "supported=False" in result.messages[0]


def test_promotion_pattern_stats_tracks_false_confirmed_warning():
    results = [
        runner.SemanticEvalResult(
            case_id="unsupported",
            question="删除率",
            tags=[],
            promotion_pattern="concept_absent_full_metadata",
            expected_warning=True,
            warning_count=1,
            warnings=[{"refutation_confirmed": True}],
        ),
        runner.SemanticEvalResult(
            case_id="positive",
            question="退货率",
            tags=["positive_schema"],
            case_type="verifier_only",
            promotion_pattern="concept_absent_full_metadata",
            required_concepts=[{"concept": "退货率", "supported": True}],
        ),
        runner.SemanticEvalResult(
            case_id="negative",
            question="退货率",
            tags=["negative_schema"],
            case_type="verifier_only",
            promotion_pattern="concept_absent_full_metadata",
            required_concepts=[{"concept": "退货率", "supported": False}],
        ),
        runner.SemanticEvalResult(
            case_id="false_positive",
            question="退款率",
            tags=[],
            promotion_pattern="concept_absent_full_metadata",
            expected_warning=False,
            warning_count=1,
            warnings=[{"refutation_confirmed": True}],
        ),
    ]

    stats = runner._promotion_pattern_stats(results)["concept_absent_full_metadata"]

    assert stats["cases"] == 4
    assert stats["confirmed_warning_cases"] == 2
    assert stats["false_confirmed_warning_cases"] == 1
    assert stats["verifier_positive_cases"] == 1
    assert stats["verifier_positive_passed"] == 1
    assert stats["verifier_negative_cases"] == 1
    assert stats["verifier_negative_passed"] == 1


def test_promotion_blocks_pattern_with_false_confirmed_refutation():
    results = [
        _pattern_case("ok", expected_warning=True, warnings=[{"refutation_confirmed": True}]),
        _pattern_case("bad", expected_warning=False, warnings=[{"refutation_confirmed": True}], passed=False),
    ]

    readiness = runner.evaluate_promotion_readiness(results, min_completed=2)

    assert readiness["p"]["promotable"] is False
    assert "false_confirmed" in readiness["p"]["reason"]


def test_promotion_requires_min_completed_observations():
    results = [_pattern_case("a", expected_warning=True, warnings=[{"refutation_confirmed": True}])]

    readiness = runner.evaluate_promotion_readiness(results, min_completed=5)

    assert readiness["p"]["promotable"] is False
    assert "insufficient" in readiness["p"]["reason"]


def test_promotion_ignores_inconclusive_in_denominator():
    results = [
        _pattern_case("a", expected_warning=True, warnings=[{"refutation_confirmed": True}]),
        _pattern_case("b", expected_warning=True, warnings=[{"refutation_confirmed": True}]),
        _pattern_case("c", inconclusive=True),
    ]

    readiness = runner.evaluate_promotion_readiness(results, min_completed=2, min_positive=0, min_negative=0)

    assert readiness["p"]["completed"] == 2
    assert readiness["p"]["promotable"] is True


def test_promotion_blocks_when_a_completed_case_failed():
    results = [
        _pattern_case("ok", expected_warning=True, warnings=[{"refutation_confirmed": True}]),
        _pattern_case("miss", expected_warning=True, warnings=[], passed=False),
    ]

    readiness = runner.evaluate_promotion_readiness(results, min_completed=2, min_positive=0, min_negative=0)

    assert readiness["p"]["promotable"] is False
    assert "failed" in readiness["p"]["reason"]


def test_promotion_requires_minimum_fixture_coverage():
    results = [
        _pattern_case(f"c{i}", expected_warning=True, warnings=[{"refutation_confirmed": True}])
        for i in range(20)
    ]

    readiness = runner.evaluate_promotion_readiness(results, min_completed=20)

    assert readiness["p"]["promotable"] is False
    assert "fixture coverage" in readiness["p"]["reason"]


def test_promotion_passes_with_completed_failures_absent_and_fixtures_present():
    positive = _pattern_case(
        "pos",
        case_type="fixture",
        tags=["positive_schema"],
        expected_warning=True,
        warnings=[{"refutation_confirmed": True, "refutation_pattern": "p"}],
    )
    negative = _pattern_case("neg", case_type="fixture", tags=["negative_schema"], expected_warning=False)
    workflow = [
        _pattern_case(
            f"w{i}",
            expected_warning=True,
            warnings=[{"refutation_confirmed": True, "refutation_pattern": "p"}],
        )
        for i in range(18)
    ]

    readiness = runner.evaluate_promotion_readiness([positive, negative, *workflow], min_completed=20)

    assert readiness["p"]["promotable"] is True


def test_promotion_does_not_credit_mismatched_refutation_pattern():
    positive = _pattern_case(
        "pos",
        case_type="fixture",
        tags=["positive_schema"],
        expected_warning=True,
        warnings=[{"refutation_confirmed": True, "refutation_pattern": "value_absent_distinct_probe"}],
    )
    negative = _pattern_case("neg", case_type="fixture", tags=["negative_schema"], expected_warning=False)
    workflow = [
        _pattern_case(
            f"w{i}",
            expected_warning=True,
            warnings=[{"refutation_confirmed": True, "refutation_pattern": "p"}],
        )
        for i in range(18)
    ]

    readiness = runner.evaluate_promotion_readiness([positive, negative, *workflow], min_completed=20)

    assert readiness["p"]["positive_fixtures_matched"] == 0
    assert readiness["p"]["promotable"] is False
    assert "fixture coverage" in readiness["p"]["reason"]


def test_write_promoted_patterns_lists_only_promotable(tmp_path):
    readiness = {"p": {"promotable": True, "reason": "ok"}, "q": {"promotable": False, "reason": "x"}}
    path = tmp_path / "promoted_patterns.json"

    runner.write_promoted_patterns(readiness, path=path)

    import json

    assert json.loads(path.read_text())["promoted"] == ["p"]


def test_availability_report_separates_and_lists_chronic_case_ids():
    results = [
        _pattern_case("a"),
        _pattern_case("a", inconclusive=True),
        _pattern_case("b", inconclusive=True),
        _pattern_case("b", inconclusive=True),
    ]

    report = runner.availability_report(results)

    assert report["completed_observations"] == 1
    assert report["inconclusive_observations"] == 3
    assert report["chronically_unavailable_case_ids"] == ["b"]


def test_run_verifier_only_case_marks_unavailable_as_inconclusive():
    class _UnavailableVerifier:
        def extract_required_concepts(self, request):
            from backend.app.agent.semantic_grounding import SemanticVerifierUnavailable

            raise SemanticVerifierUnavailable("down")

    result = runner.SemanticEvalResult(case_id="v", question="q", tags=[], case_type="verifier_only")

    runner._run_verifier_only_case(
        result,
        {"full_schema_context": "# Schema"},
        verifier=_UnavailableVerifier(),
    )

    assert result.status == "inconclusive"


def test_run_verifier_only_case_treats_malformed_response_as_inconclusive():
    class _MalformedVerifier:
        def extract_required_concepts(self, request):
            raise ValueError("bad verifier payload")

    result = runner.SemanticEvalResult(case_id="v", question="q", tags=[], case_type="verifier_only")

    runner._run_verifier_only_case(
        result,
        {"full_schema_context": "# Schema"},
        verifier=_MalformedVerifier(),
    )

    assert result.status == "inconclusive"


def test_run_fixture_case_runs_grounding_and_refutation_on_pinned_sql():
    class _FakeVerifier:
        def extract_required_concepts(self, request):
            from backend.app.agent.semantic_grounding import ConceptExtractionResult, RequiredConcept

            return ConceptExtractionResult(
                concepts=(RequiredConcept(concept="删除率", concept_id="c1", supported=False),)
            )

        def check_grounding(self, request):
            from backend.app.agent.semantic_grounding import GroundingCheckResult, SemanticGroundingIssue

            return GroundingCheckResult(
                ok=False,
                issues=(SemanticGroundingIssue(concept="删除率", failure_kind="substituted", concept_id="c1"),),
            )

    class _FakeAuditor:
        def evidence(self, *, datasource_name):
            return object()

        def audit(self, issue, *, evidence, concept=None):
            from backend.app.agent.semantic_grounding import RefutationAuditResult

            return RefutationAuditResult(
                confirmed=True,
                reason="absent",
                pattern="concept_absent_full_metadata",
            )

    result = runner.SemanticEvalResult(
        case_id="f1",
        question="查看删除率趋势",
        tags=["fixture", "positive_schema"],
        case_type="fixture",
        promotion_pattern="concept_absent_full_metadata",
    )
    case = {
        "id": "f1",
        "question": "查看删除率趋势",
        "full_schema_context": "# Tables\n- fact_orders",
        "sql": "SELECT countIf(order_status='refunded')/count(*) FROM fact_orders",
        "expected": {
            "warning": True,
            "refutation_confirmed": True,
            "refutation_pattern": "concept_absent_full_metadata",
        },
    }

    runner._run_fixture_case(result, case, verifier=_FakeVerifier(), auditor=_FakeAuditor())

    assert result.warning_count == 1
    assert result.warnings[0]["refutation_confirmed"] is True
    assert result.passed is True
