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
