from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.agent.state import AgentState, GroundingWarningPayload
from backend.app.config import get_settings, semantic_guard_mode
from backend.app.core.llm_provider import strip_code_fence
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.service import build_schema_context


logger = logging.getLogger(__name__)
FullSchemaContextBuilder = Callable[..., str]


class SemanticVerifierUnavailable(RuntimeError):
    """Raised when semantic grounding verification cannot produce a reliable judgment."""


@dataclass(frozen=True)
class RequiredConcept:
    concept: str
    concept_id: str = ""
    concept_type: str = "other"
    supported: bool = False
    evidence: tuple[str, ...] = ()
    explanation: str = ""

    def model_dump(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SemanticGroundingIssue:
    concept: str
    failure_kind: str
    concept_id: str = ""
    concept_type: str = "other"
    sql_mapping: str | None = None
    supported: bool = False
    explanation: str = ""

    def model_dump(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConceptExtractionRequest:
    question: str
    full_schema_context: str
    datasource_name: str = DEFAULT_DATASOURCE
    datasource_dialect: str = "duckdb"


@dataclass(frozen=True)
class ConceptExtractionResult:
    concepts: tuple[RequiredConcept, ...]


@dataclass(frozen=True)
class GroundingCheckRequest:
    question: str
    sql: str
    concepts: tuple[RequiredConcept, ...]
    sql_facts: "SQLSemanticFacts"
    datasource_name: str = DEFAULT_DATASOURCE
    datasource_dialect: str = "duckdb"


@dataclass(frozen=True)
class GroundingCheckResult:
    ok: bool
    issues: tuple[SemanticGroundingIssue, ...] = ()


@dataclass(frozen=True)
class RefutationAuditResult:
    confirmed: bool
    reason: str

    def model_dump(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SQLSemanticFacts:
    forced_empty_result: bool = False
    forced_empty_reason: str | None = None

    def model_dump(self) -> dict:
        return asdict(self)


class SemanticGroundingVerifier(Protocol):
    def extract_required_concepts(self, request: ConceptExtractionRequest) -> ConceptExtractionResult:
        ...

    def check_grounding(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        ...


class UnavailableSemanticGroundingVerifier:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def extract_required_concepts(self, request: ConceptExtractionRequest) -> ConceptExtractionResult:
        raise SemanticVerifierUnavailable(self.reason)

    def check_grounding(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        raise SemanticVerifierUnavailable(self.reason)


class SemanticRefutationAuditor:
    """Corroborates verifier findings from full schema evidence; it never interprets the question."""

    def __init__(
        self,
        full_schema_context_builder: FullSchemaContextBuilder = build_schema_context,
    ) -> None:
        self._full_schema_context_builder = full_schema_context_builder

    def full_schema_context(self, *, datasource_name: str) -> str:
        return self._full_schema_context_builder(datasource_name=datasource_name)

    def audit(self, issue: SemanticGroundingIssue, *, full_schema_context: str) -> RefutationAuditResult:
        concept = issue.concept.strip()
        if not concept:
            return RefutationAuditResult(
                confirmed=False,
                reason="No requested concept was provided by the verifier.",
            )
        # TODO(phase-2): replace this broad evidence search with structured,
        # channel-by-channel refutation over tables, columns, aliases, metrics,
        # verified queries, guidance, sample values, and datasource-bound overlay
        # assets before enabling confirmed refutations as hard-block evidence.
        #
        # TODO(phase-2): add the SELECT DISTINCT confirmation path for requested
        # value-level concepts that are absent from sample-value fallbacks. Phase 1
        # is warn-only, so this audit result is observation data, not a block gate.
        if _concept_has_schema_evidence(concept, full_schema_context):
            return RefutationAuditResult(
                confirmed=False,
                reason=f"Full datasource metadata contains evidence for {concept!r}; deterministic audit abstained.",
            )
        return RefutationAuditResult(
            confirmed=True,
            reason=f"Full datasource metadata contains no evidence for {concept!r}.",
        )


def semantic_guard_node(
    state: AgentState,
    *,
    verifier: SemanticGroundingVerifier | None = None,
    auditor: SemanticRefutationAuditor | None = None,
    mode: str | None = None,
) -> AgentState:
    mode = _normalize_mode(mode or "off")
    if mode == "off":
        return state
    if state.sql is None:
        raise ValueError("sql is required before semantic guard.")

    verifier = verifier or UnavailableSemanticGroundingVerifier("Semantic verifier is not configured.")
    auditor = auditor or SemanticRefutationAuditor()
    sql_facts = analyze_sql_semantic_facts(state.sql, datasource_dialect=state.datasource_dialect)

    try:
        full_context = _full_schema_context(state, auditor)
        concepts = _required_concepts(state, verifier, full_context)
        unsupported_concepts = tuple(concept for concept in concepts if not concept.supported)
        if not unsupported_concepts:
            state.semantic_guard_result = {
                "ok": True,
                "verifier_unavailable": False,
                "issues": [],
            }
            state.completed_steps.append("semantic_guard")
            return state
        result = _forced_empty_grounding_result(unsupported_concepts, sql_facts) or verifier.check_grounding(
            GroundingCheckRequest(
                question=state.question,
                sql=state.sql,
                concepts=unsupported_concepts,
                sql_facts=sql_facts,
                datasource_name=state.datasource_name,
                datasource_dialect=state.datasource_dialect,
            )
        )
    except SemanticVerifierUnavailable as exc:
        _record_verifier_unavailable(state, mode=mode, reason=str(exc))
        return state
    except Exception as exc:
        logger.warning("Semantic grounding verifier failed open.", exc_info=True)
        _record_verifier_unavailable(state, mode=mode, reason=str(exc))
        return state

    result = _normalize_grounding_result(result, unsupported_concepts)
    state.semantic_guard_result = {
        "ok": result.ok,
        "verifier_unavailable": False,
        "issues": [issue.model_dump() for issue in result.issues],
        "sql_facts": sql_facts.model_dump(),
    }
    state.completed_steps.append("semantic_guard")
    if result.ok:
        return state

    for issue in result.issues:
        refutation = auditor.audit(issue, full_schema_context=full_context)
        state.grounding_warnings.append(_warning_from_issue(issue, refutation))

    if mode == "enforce" and any(warning.get("refutation_confirmed") for warning in state.grounding_warnings):
        state.stopped_at = "semantic_guard"
        state.error = _semantic_block_message(state.grounding_warnings)
    return state


def semantic_guard_mode_value(value: str | None = None) -> str:
    return semantic_guard_mode(get_settings()) if value is None else _normalize_mode(value)


def parse_concept_extraction_content(content: str) -> ConceptExtractionResult:
    payload = _json_object(content, "Semantic concept extraction response must be a JSON object.")
    raw_concepts = payload.get("concepts")
    if not isinstance(raw_concepts, list):
        raise ValueError("Semantic concept extraction response must include concepts.")
    return ConceptExtractionResult(
        concepts=_ensure_concept_ids(
            tuple(_parse_required_concept(item) for item in raw_concepts)
        )
    )


def parse_grounding_check_content(content: str) -> GroundingCheckResult:
    payload = _json_object(content, "Semantic grounding response must be a JSON object.")
    raw_ok = payload.get("ok")
    if not isinstance(raw_ok, bool):
        raise ValueError("Semantic grounding response must include boolean ok.")
    raw_issues = payload.get("issues") or []
    if not isinstance(raw_issues, list):
        raise ValueError("Semantic grounding response issues must be a list.")
    return GroundingCheckResult(
        ok=raw_ok,
        issues=tuple(_parse_grounding_issue(item) for item in raw_issues),
    )


def _required_concepts(
    state: AgentState,
    verifier: SemanticGroundingVerifier,
    full_schema_context: str,
) -> tuple[RequiredConcept, ...]:
    if state.required_concepts is not None:
        concepts = tuple(_coerce_required_concept(item) for item in state.required_concepts)
        return concepts if all(concept.concept_id for concept in concepts) else _ensure_concept_ids(concepts)
    result = verifier.extract_required_concepts(
        ConceptExtractionRequest(
            question=state.question,
            full_schema_context=full_schema_context,
            datasource_name=state.datasource_name,
            datasource_dialect=state.datasource_dialect,
        )
    )
    concepts = _ensure_concept_ids(result.concepts)
    state.required_concepts = [concept.model_dump() for concept in concepts]
    return concepts


def analyze_sql_semantic_facts(sql: str, *, datasource_dialect: str = "duckdb") -> SQLSemanticFacts:
    try:
        tree = sqlglot.parse_one(sql, read=datasource_dialect or "duckdb")
    except SqlglotError:
        return SQLSemanticFacts()

    limit = tree.args.get("limit")
    if isinstance(limit, exp.Limit) and _is_zero_literal(limit.args.get("expression")):
        return SQLSemanticFacts(forced_empty_result=True, forced_empty_reason="LIMIT 0")

    where = tree.args.get("where")
    if isinstance(where, exp.Where) and _is_forced_false_expression(where.this):
        return SQLSemanticFacts(forced_empty_result=True, forced_empty_reason=f"WHERE {where.this.sql()}")

    return SQLSemanticFacts()


def _full_schema_context(state: AgentState, auditor: SemanticRefutationAuditor) -> str:
    if state.full_schema_context is None:
        state.full_schema_context = auditor.full_schema_context(datasource_name=state.datasource_name)
    return state.full_schema_context


def _record_verifier_unavailable(state: AgentState, *, mode: str, reason: str) -> None:
    logger.warning("Semantic grounding verifier unavailable; failing open: %s", reason)
    state.semantic_guard_result = {
        "ok": None,
        "verifier_unavailable": True,
        "reason": reason,
        "issues": [],
    }
    if mode == "enforce":
        state.grounding_warnings.append(
            {
                "concept": "semantic_verifier",
                "failure_kind": "verifier_unavailable",
                "sql_mapping": None,
                "supported": False,
                "refutation_confirmed": False,
                "refutation_reason": reason,
                "message": "Semantic grounding verifier was unavailable; this result was not semantically checked.",
            }
        )


def _warning_from_issue(issue: SemanticGroundingIssue, refutation: RefutationAuditResult) -> GroundingWarningPayload:
    action = "used a proxy" if issue.failure_kind == "substituted" else "omitted the concept"
    if issue.failure_kind == "substituted" and issue.sql_mapping:
        detail = f" SQL mapping: {issue.sql_mapping}."
    else:
        detail = ""
    return {
        "concept": issue.concept,
        "concept_id": issue.concept_id,
        "concept_type": issue.concept_type,
        "failure_kind": issue.failure_kind,
        "sql_mapping": issue.sql_mapping,
        "supported": issue.supported,
        "explanation": issue.explanation,
        "refutation_confirmed": refutation.confirmed,
        "refutation_reason": refutation.reason,
        "message": f"The question asked for {issue.concept!r}, but the SQL {action}.{detail} {issue.explanation}".strip(),
    }


def _semantic_block_message(warnings: list[dict]) -> str:
    concepts = ", ".join(str(warning.get("concept")) for warning in warnings if warning.get("refutation_confirmed"))
    return f'当前 schema 中没有"{concepts}"对应的字段、状态值或指标，无法安全生成 SQL。'


def _concept_has_schema_evidence(concept: str, full_schema_context: str) -> bool:
    # TODO(phase-2): this intentionally blunt substring corroboration is only
    # acceptable while the deterministic audit is observation-only. Enforcement
    # needs structured evidence by channel and broader Unicode coverage.
    normalized_concept = _normalize_evidence_text(concept)
    if not normalized_concept:
        return False
    return normalized_concept in _normalize_evidence_text(full_schema_context)


def _normalize_evidence_text(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", text.casefold()))


def _json_object(content: str, error_message: str) -> dict:
    stripped = strip_code_fence(content)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        raise ValueError(error_message) from None
    if not isinstance(payload, dict):
        raise ValueError(error_message)
    return payload


def _parse_required_concept(value: object) -> RequiredConcept:
    if not isinstance(value, dict):
        raise ValueError("Each semantic concept must be an object.")
    return RequiredConcept(
        concept=_required_string(value.get("concept"), "concept"),
        concept_id=_optional_string(value.get("concept_id"), ""),
        concept_type=_optional_string(value.get("concept_type"), "other"),
        supported=bool(value.get("supported", False)),
        evidence=tuple(_string_list(value.get("evidence"))),
        explanation=_optional_string(value.get("explanation"), ""),
    )


def _parse_grounding_issue(value: object) -> SemanticGroundingIssue:
    if not isinstance(value, dict):
        raise ValueError("Each semantic grounding issue must be an object.")
    failure_kind = _optional_string(value.get("failure_kind"), "omitted")
    if failure_kind not in {"substituted", "omitted"}:
        failure_kind = "omitted"
    sql_mapping = value.get("sql_mapping")
    concept = _optional_string(value.get("concept"), "")
    concept_id = _optional_string(value.get("concept_id"), "")
    if not concept and not concept_id:
        raise ValueError("Semantic grounding issue must include concept_id or concept.")
    return SemanticGroundingIssue(
        concept=concept,
        failure_kind=failure_kind,
        concept_id=concept_id,
        concept_type=_optional_string(value.get("concept_type"), "other"),
        sql_mapping=sql_mapping if isinstance(sql_mapping, str) and sql_mapping.strip() else None,
        supported=bool(value.get("supported", False)),
        explanation=_optional_string(value.get("explanation"), ""),
    )


def _coerce_required_concept(value: dict | RequiredConcept) -> RequiredConcept:
    if isinstance(value, RequiredConcept):
        return value
    return _parse_required_concept(value)


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Semantic grounding field {field_name} must be a non-empty string.")
    return value.strip()


def _optional_string(value: object, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalize_mode(value: str) -> str:
    normalized = str(value or "off").strip().casefold()
    return normalized if normalized in {"off", "warn", "enforce"} else "off"


def _ensure_concept_ids(concepts: tuple[RequiredConcept, ...]) -> tuple[RequiredConcept, ...]:
    return tuple(
        concept
        if concept.concept_id
        else RequiredConcept(
            concept=concept.concept,
            concept_id=f"c{index + 1}",
            concept_type=concept.concept_type,
            supported=concept.supported,
            evidence=concept.evidence,
            explanation=concept.explanation,
        )
        for index, concept in enumerate(concepts)
    )


def _normalize_grounding_result(
    result: GroundingCheckResult,
    concepts: tuple[RequiredConcept, ...],
) -> GroundingCheckResult:
    by_id = {concept.concept_id: concept for concept in concepts}
    by_name = {concept.concept: concept for concept in concepts}
    normalized_issues = []
    for issue in result.issues:
        concept = by_id.get(issue.concept_id) or by_name.get(issue.concept)
        if concept is None:
            normalized_issues.append(issue)
            continue
        normalized_issues.append(
            SemanticGroundingIssue(
                concept=concept.concept,
                concept_id=concept.concept_id,
                concept_type=concept.concept_type,
                failure_kind=issue.failure_kind,
                sql_mapping=issue.sql_mapping,
                supported=issue.supported,
                explanation=issue.explanation,
            )
        )
    return GroundingCheckResult(ok=result.ok, issues=tuple(normalized_issues))


def _forced_empty_grounding_result(
    unsupported_concepts: tuple[RequiredConcept, ...],
    sql_facts: SQLSemanticFacts,
) -> GroundingCheckResult | None:
    if not sql_facts.forced_empty_result:
        return None
    return GroundingCheckResult(
        ok=False,
        issues=tuple(
            SemanticGroundingIssue(
                concept=concept.concept,
                concept_id=concept.concept_id,
                concept_type=concept.concept_type,
                failure_kind="omitted",
                sql_mapping=sql_facts.forced_empty_reason,
                explanation=(
                    "The SQL is forced to return an empty result, which does not ground "
                    "the unsupported requested concept."
                ),
            )
            for concept in unsupported_concepts
        ),
    )


def _is_forced_false_expression(expression: exp.Expression) -> bool:
    if isinstance(expression, exp.Paren):
        return _is_forced_false_expression(expression.this)
    if isinstance(expression, exp.Boolean):
        return expression.this is False
    if isinstance(expression, exp.EQ):
        left = expression.args.get("this")
        right = expression.args.get("expression")
        if _is_literal(left) and _is_literal(right):
            return left.this != right.this
    if isinstance(expression, exp.And):
        left = expression.args.get("this")
        right = expression.args.get("expression")
        return (
            isinstance(left, exp.Expression)
            and _is_forced_false_expression(left)
        ) or (
            isinstance(right, exp.Expression)
            and _is_forced_false_expression(right)
        )
    if isinstance(expression, exp.Or):
        left = expression.args.get("this")
        right = expression.args.get("expression")
        return (
            isinstance(left, exp.Expression)
            and isinstance(right, exp.Expression)
            and _is_forced_false_expression(left)
            and _is_forced_false_expression(right)
        )
    return False


def _is_literal(value: object) -> bool:
    return isinstance(value, exp.Literal)


def _is_zero_literal(value: object) -> bool:
    return isinstance(value, exp.Literal) and not value.is_string and value.this == "0"
