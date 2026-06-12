import pytest

from backend.app.agent.semantic_grounding import (
    ConceptExtractionRequest,
    ConceptExtractionResult,
    GroundingCheckRequest,
    GroundingCheckResult,
    RefutationAuditResult,
    RequiredConcept,
    SemanticGroundingIssue,
    SemanticRefutationAuditor,
    SemanticVerifierUnavailable,
    analyze_sql_semantic_facts,
    parse_concept_extraction_content,
    parse_grounding_check_content,
    semantic_guard_node,
)
from backend.app.agent.state import AgentState


def test_parse_concept_extraction_content_accepts_structured_json():
    result = parse_concept_extraction_content(
        """
        {
          "concepts": [
            {
              "concept": "删除率",
              "concept_type": "metric",
              "supported": false,
              "evidence": [],
              "explanation": "No deletion concept exists."
            }
          ]
        }
        """
    )

    assert result.concepts == (
        RequiredConcept(
            concept="删除率",
            concept_id="c1",
            concept_type="metric",
            supported=False,
            evidence=(),
            explanation="No deletion concept exists.",
        ),
    )


def test_parse_grounding_check_content_accepts_substitution_and_omission():
    result = parse_grounding_check_content(
        """
        {
          "ok": false,
          "issues": [
            {
              "concept": "删除率",
              "failure_kind": "substituted",
              "sql_mapping": "order_status = 'refunded'",
              "supported": false,
              "explanation": "Proxy status value."
            },
            {
              "concept": "删除的订单",
              "failure_kind": "omitted",
              "sql_mapping": null,
              "supported": false,
              "explanation": "No deleted filter."
            }
          ]
        }
        """
    )

    assert result.ok is False
    assert [issue.failure_kind for issue in result.issues] == ["substituted", "omitted"]
    assert result.issues[0].sql_mapping == "order_status = 'refunded'"
    assert result.issues[1].sql_mapping is None


def test_parse_grounding_check_content_requires_issue_identifier():
    with pytest.raises(ValueError, match="concept_id or concept"):
        parse_grounding_check_content(
            """
            {
              "ok": false,
              "issues": [
                { "failure_kind": "omitted" }
              ]
            }
            """
        )


def test_refutation_auditor_confirms_absent_requested_concept():
    auditor = SemanticRefutationAuditor(full_schema_context_builder=lambda datasource_name: "# Tables\n- fact_orders")

    result = auditor.audit(
        SemanticGroundingIssue(concept="删除率", failure_kind="substituted"),
        full_schema_context=auditor.full_schema_context(datasource_name="duckdb_ecommerce"),
    )

    assert result.confirmed is True
    assert "no evidence" in result.reason


def test_refutation_auditor_abstains_when_full_metadata_has_evidence():
    auditor = SemanticRefutationAuditor(
        full_schema_context_builder=lambda datasource_name: "# Metric Definitions\n- 退款率 (refund_rate)"
    )

    result = auditor.audit(
        SemanticGroundingIssue(concept="退款率", failure_kind="substituted"),
        full_schema_context=auditor.full_schema_context(datasource_name="duckdb_ecommerce"),
    )

    assert result.confirmed is False
    assert "abstained" in result.reason


def test_semantic_guard_warn_mode_records_visible_warning():
    state = AgentState(
        question="查看删除率趋势",
        sql="SELECT countIf(order_status = 'refunded') AS deletion_rate FROM fact_orders",
    )
    verifier = _FakeSemanticVerifier(
        concepts=[RequiredConcept(concept="删除率", concept_type="metric", supported=False)],
        checks=[
            GroundingCheckResult(
                ok=False,
                issues=(
                    SemanticGroundingIssue(
                        concept="删除率",
                        failure_kind="substituted",
                        sql_mapping="order_status = 'refunded'",
                        explanation="Refunded is a proxy.",
                    ),
                ),
            )
        ],
    )
    auditor = _FakeAuditor(RefutationAuditResult(confirmed=True, reason="No evidence."))

    semantic_guard_node(state, verifier=verifier, auditor=auditor, mode="warn")

    assert state.stopped_at is None
    assert state.grounding_warnings[0]["concept"] == "删除率"
    assert state.grounding_warnings[0]["failure_kind"] == "substituted"
    assert state.grounding_warnings[0]["refutation_confirmed"] is True
    assert state.completed_steps == ["semantic_guard"]


def test_semantic_guard_normalizes_issue_from_concept_id():
    state = AgentState(question="查看删除率趋势", sql="SELECT 1")
    verifier = _FakeSemanticVerifier(
        concepts=[RequiredConcept(concept="删除率", concept_type="metric", supported=False)],
        checks=[
            GroundingCheckResult(
                ok=False,
                issues=(
                    SemanticGroundingIssue(
                        concept="",
                        concept_id="c1",
                        failure_kind="omitted",
                        explanation="No mapping.",
                    ),
                ),
            )
        ],
    )

    semantic_guard_node(
        state,
        verifier=verifier,
        auditor=_FakeAuditor(RefutationAuditResult(confirmed=True, reason="No evidence.")),
        mode="warn",
    )

    assert state.grounding_warnings[0]["concept"] == "删除率"
    assert state.grounding_warnings[0]["concept_id"] == "c1"
    assert state.grounding_warnings[0]["concept_type"] == "metric"


def test_semantic_guard_caches_extraction_across_candidates():
    state = AgentState(question="删除的订单", sql="SELECT order_id FROM fact_orders")
    verifier = _FakeSemanticVerifier(
        concepts=[RequiredConcept(concept="删除的订单", concept_type="filter", supported=False)],
        checks=[
            GroundingCheckResult(
                ok=False,
                issues=(SemanticGroundingIssue(concept="删除的订单", failure_kind="omitted"),),
            ),
            GroundingCheckResult(
                ok=False,
                issues=(SemanticGroundingIssue(concept="删除的订单", failure_kind="omitted"),),
            ),
        ],
    )
    auditor = _FakeAuditor(RefutationAuditResult(confirmed=True, reason="No evidence."))

    semantic_guard_node(state, verifier=verifier, auditor=auditor, mode="warn")
    state.semantic_guard_result = None
    state.grounding_warnings = []
    state.sql = "SELECT order_id FROM fact_orders LIMIT 20"
    semantic_guard_node(state, verifier=verifier, auditor=auditor, mode="warn")

    assert verifier.extraction_calls == 1
    assert verifier.check_calls == 2
    assert state.required_concepts is not None


def test_semantic_guard_skips_grounding_call_when_all_concepts_supported():
    state = AgentState(question="查看退款率", sql="SELECT 1")
    verifier = _FakeSemanticVerifier(
        concepts=[RequiredConcept(concept="退款率", concept_type="metric", supported=True)],
        checks=[],
    )

    semantic_guard_node(
        state,
        verifier=verifier,
        auditor=_FakeAuditor(RefutationAuditResult(confirmed=False, reason="Evidence exists.")),
        mode="warn",
    )

    assert verifier.extraction_calls == 1
    assert verifier.check_calls == 0
    assert state.semantic_guard_result == {
        "ok": True,
        "verifier_unavailable": False,
        "issues": [],
    }
    assert state.completed_steps == ["semantic_guard"]


def test_semantic_guard_fail_open_on_verifier_unavailable():
    state = AgentState(question="查看删除率趋势", sql="SELECT 1")

    semantic_guard_node(
        state,
        verifier=_UnavailableVerifier(),
        auditor=_FakeAuditor(RefutationAuditResult(confirmed=True, reason="No evidence.")),
        mode="warn",
    )

    assert state.stopped_at is None
    assert state.grounding_warnings == []
    assert state.semantic_guard_result == {
        "ok": None,
        "verifier_unavailable": True,
        "reason": "timeout",
        "issues": [],
    }
    assert state.completed_steps == []


def test_semantic_guard_marks_forced_empty_sql_as_omission_without_grounding_call():
    state = AgentState(question="删除的订单", sql="SELECT order_id FROM fact_orders WHERE FALSE")
    verifier = _FakeSemanticVerifier(
        concepts=[RequiredConcept(concept="删除的订单", concept_type="filter", supported=False)],
        checks=[],
    )

    semantic_guard_node(
        state,
        verifier=verifier,
        auditor=_FakeAuditor(RefutationAuditResult(confirmed=True, reason="No evidence.")),
        mode="warn",
    )

    assert verifier.extraction_calls == 1
    assert verifier.check_calls == 0
    assert state.semantic_guard_result is not None
    assert state.semantic_guard_result["sql_facts"]["forced_empty_result"] is True
    assert state.grounding_warnings[0]["concept"] == "删除的订单"
    assert state.grounding_warnings[0]["failure_kind"] == "omitted"
    assert state.grounding_warnings[0]["sql_mapping"].startswith("WHERE")


def test_analyze_sql_semantic_facts_detects_forced_empty_queries():
    assert analyze_sql_semantic_facts("SELECT * FROM fact_orders WHERE 1 = 0").forced_empty_result is True
    assert analyze_sql_semantic_facts("SELECT * FROM fact_orders LIMIT 0").forced_empty_result is True
    assert (
        analyze_sql_semantic_facts("SELECT * FROM fact_orders WHERE 1 = 0 OR 2 = 2").forced_empty_result
        is False
    )


class _FakeSemanticVerifier:
    def __init__(self, concepts: list[RequiredConcept], checks: list[GroundingCheckResult]) -> None:
        self._concepts = tuple(concepts)
        self._checks = list(checks)
        self.extraction_calls = 0
        self.check_calls = 0

    def extract_required_concepts(self, request: ConceptExtractionRequest) -> ConceptExtractionResult:
        self.extraction_calls += 1
        return ConceptExtractionResult(concepts=self._concepts)

    def check_grounding(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        self.check_calls += 1
        if not self._checks:
            raise AssertionError("No scripted grounding checks left.")
        return self._checks.pop(0)


class _UnavailableVerifier:
    def extract_required_concepts(self, request: ConceptExtractionRequest) -> ConceptExtractionResult:
        raise SemanticVerifierUnavailable("timeout")

    def check_grounding(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        raise AssertionError("check_grounding should not be called")


class _FakeAuditor:
    def __init__(self, result: RefutationAuditResult) -> None:
        self._result = result

    def full_schema_context(self, *, datasource_name: str) -> str:
        return "# Full Schema Context"

    def audit(self, issue: SemanticGroundingIssue, *, full_schema_context: str) -> RefutationAuditResult:
        return self._result
