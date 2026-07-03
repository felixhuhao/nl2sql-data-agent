from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol, TypeGuard

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.agent.state import AgentState, GroundingWarningPayload
from backend.app.agent.schema_evidence import SchemaEvidence, build_schema_evidence, normalize_evidence_text
from backend.app.agent.promoted_patterns import is_pattern_promoted
from backend.app.config import get_settings, semantic_guard_mode
from backend.app.core.llm_provider import strip_code_fence
from backend.app.i18n import t
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.service import build_schema_context


logger = logging.getLogger(__name__)
FullSchemaContextBuilder = Callable[..., str]
SchemaEvidenceBuilder = Callable[..., SchemaEvidence]
DistinctValueExecutor = Callable[..., tuple[str, ...]]
DISTINCT_PROBE_LIMIT = 1000


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
    target_table: str = ""
    target_column: str = ""
    requested_value: str = ""

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
    pattern: str = ""

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


def _default_distinct_executor(table_name: str, column_name: str, *, datasource_name: str) -> tuple[str, ...]:
    from backend.app.connectors.registry import get_datasource_dialect
    from backend.app.execution.runner import execute_guarded_sql
    from backend.app.sql_guard.guard import guard_sql
    from backend.app.sql_guard.scope import build_default_guard_scope

    dialect = get_datasource_dialect(datasource_name)
    table_sql = exp.to_identifier(table_name, quoted=True).sql(dialect=dialect)
    column_sql = exp.to_identifier(column_name, quoted=True).sql(dialect=dialect)
    sql = f"SELECT DISTINCT {column_sql} FROM {table_sql} LIMIT {DISTINCT_PROBE_LIMIT}"
    guard_result = guard_sql(
        sql,
        build_default_guard_scope(datasource_name=datasource_name),
        datasource_name=datasource_name,
        max_result_rows=DISTINCT_PROBE_LIMIT,
    )
    if not guard_result.allowed:
        raise SemanticVerifierUnavailable(f"DISTINCT probe rejected by guard: {guard_result.reason}")
    query_result = execute_guarded_sql(guard_result, datasource_name=datasource_name)
    return tuple(str(row[0]) for row in query_result.rows if row)


class SemanticRefutationAuditor:
    """Corroborates verifier findings from full schema evidence; it never interprets the question."""

    def __init__(
        self,
        full_schema_context_builder: FullSchemaContextBuilder = build_schema_context,
        evidence_builder: SchemaEvidenceBuilder = build_schema_evidence,
        distinct_executor: DistinctValueExecutor = _default_distinct_executor,
    ) -> None:
        self._full_schema_context_builder = full_schema_context_builder
        self._evidence_builder = evidence_builder
        self._distinct_executor = distinct_executor

    def full_schema_context(self, *, datasource_name: str) -> str:
        return self._full_schema_context_builder(datasource_name=datasource_name)

    def evidence(self, *, datasource_name: str) -> SchemaEvidence:
        return self._evidence_builder(datasource_name=datasource_name)

    def audit(
        self,
        issue: SemanticGroundingIssue,
        *,
        evidence: SchemaEvidence,
        concept: RequiredConcept | None = None,
    ) -> RefutationAuditResult:
        requested_concept = issue.concept.strip()
        if not requested_concept:
            return RefutationAuditResult(
                confirmed=False,
                reason="No requested concept was provided by the verifier.",
            )
        if (
            concept is not None
            and concept.target_column
            and concept.requested_value
        ):
            return self._audit_value(requested_concept, concept, evidence)
        mapped_value_concept = _value_concept_from_issue_mapping(issue, concept, evidence)
        if mapped_value_concept is not None:
            return self._audit_value(requested_concept, mapped_value_concept, evidence)
        if evidence.has_concept_evidence(requested_concept):
            return RefutationAuditResult(
                confirmed=False,
                reason=f"Full datasource metadata contains evidence for {requested_concept!r}; deterministic audit abstained.",
            )
        return RefutationAuditResult(
            confirmed=True,
            reason=f"Full datasource metadata contains no evidence for {requested_concept!r} across any channel.",
            pattern="concept_absent_full_metadata",
        )

    def _audit_value(
        self,
        issue_concept: str,
        concept: RequiredConcept,
        evidence: SchemaEvidence,
    ) -> RefutationAuditResult:
        table, column, value = _validated_target(concept, evidence)
        if table is None:
            return RefutationAuditResult(
                confirmed=False,
                reason="Value target was not uniquely validated in metadata; deterministic audit abstained.",
            )
        if evidence.has_concept_evidence(value) or evidence.has_concept_evidence(issue_concept):
            return RefutationAuditResult(
                confirmed=False,
                reason=f"Full datasource metadata contains evidence for {value!r}; deterministic audit abstained.",
            )
        if evidence.has_sample_value(column, value):
            return RefutationAuditResult(
                confirmed=False,
                reason=f"{value!r} is an enumerated sample value of {column!r}; deterministic audit abstained.",
            )
        try:
            actual_values = self._distinct_executor(table, column, datasource_name=evidence.datasource_name)
        except Exception as exc:
            return RefutationAuditResult(
                confirmed=False,
                reason=f"DISTINCT probe unavailable for {column!r}: {exc}; deterministic audit abstained.",
            )
        if evidence.value_matches(value, actual_values):
            return RefutationAuditResult(
                confirmed=False,
                reason=f"{value!r} exists in {column!r}; deterministic audit abstained.",
            )
        return RefutationAuditResult(
            confirmed=True,
            reason=f"{value!r} is absent from {table!r}.{column!r} (DISTINCT probe).",
            pattern="value_absent_distinct_probe",
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

    try:
        evidence = _schema_evidence(state, auditor)
    except Exception as exc:
        logger.warning("Semantic refutation audit failed open.", exc_info=True)
        refutation = RefutationAuditResult(
            confirmed=False,
            reason=f"Schema evidence unavailable: {exc}; deterministic audit abstained.",
        )
        for issue in result.issues:
            state.grounding_warnings.append(_warning_from_issue(issue, refutation, locale=state.locale))
        return state

    for issue in result.issues:
        refutation = auditor.audit(
            issue,
            evidence=evidence,
            concept=_concept_for_issue(issue, unsupported_concepts),
        )
        state.grounding_warnings.append(_warning_from_issue(issue, refutation, locale=state.locale))

    blocking_warnings = [
        warning
        for warning in state.grounding_warnings
        if warning.get("refutation_confirmed") and is_pattern_promoted(warning.get("refutation_pattern"))
    ]
    if mode == "enforce" and blocking_warnings:
        state.stopped_at = "semantic_guard"
        state.error = _semantic_block_message(blocking_warnings, locale=state.locale)
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


def _schema_evidence(state: AgentState, auditor: SemanticRefutationAuditor) -> SchemaEvidence:
    if state.schema_evidence is None:
        state.schema_evidence = auditor.evidence(datasource_name=state.datasource_name)
    return state.schema_evidence


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
                "message": t("guard.semantic_verifier_unavailable", state.locale),
            }
        )


def _warning_from_issue(
    issue: SemanticGroundingIssue,
    refutation: RefutationAuditResult,
    *,
    locale: str | None = None,
) -> GroundingWarningPayload:
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
        "refutation_pattern": refutation.pattern,
        "message": t(
            "guard.semantic_warning",
            locale,
            concept=repr(issue.concept),
            action=action,
            detail=detail,
            explanation=issue.explanation,
        ).strip(),
    }


def _semantic_block_message(warnings: list[GroundingWarningPayload], *, locale: str | None = None) -> str:
    concepts = ", ".join(str(warning.get("concept")) for warning in warnings if warning.get("refutation_confirmed"))
    return t("guard.semantic_block", locale, concepts=concepts)


def _validated_target(concept: RequiredConcept, evidence: SchemaEvidence) -> tuple[str | None, str, str]:
    table = concept.target_table.strip()
    column = concept.target_column.strip()
    value = concept.requested_value.strip()
    if not column or not value:
        return None, column, value
    if table:
        return (table, column, value) if evidence.has_column(table, column) else (None, column, value)
    unique_table = evidence.unique_table_for_column(column)
    return (unique_table, column, value) if unique_table else (None, column, value)


def _value_concept_from_issue_mapping(
    issue: SemanticGroundingIssue,
    concept: RequiredConcept | None,
    evidence: SchemaEvidence,
) -> RequiredConcept | None:
    if not issue.sql_mapping:
        return None
    from backend.app.connectors.registry import get_datasource_dialect

    mapped = _single_value_predicate(
        issue.sql_mapping,
        datasource_dialect=get_datasource_dialect(evidence.datasource_name),
    )
    if mapped is None:
        return None
    table_hint, column, value = mapped
    requested_parts = [issue.concept]
    if concept is not None and concept.concept != issue.concept:
        requested_parts.append(concept.concept)
    requested_text = " ".join(part for part in requested_parts if part)
    normalized_value = normalize_evidence_text(value)
    if not normalized_value or normalized_value not in normalize_evidence_text(requested_text):
        return None
    table = table_hint if table_hint and evidence.has_column(table_hint, column) else evidence.unique_table_for_column(column)
    if table is None:
        return None
    return RequiredConcept(
        concept=issue.concept,
        concept_id=issue.concept_id,
        concept_type="value",
        supported=False,
        target_table=table,
        target_column=column,
        requested_value=value,
    )


def _single_value_predicate(sql_mapping: str, *, datasource_dialect: str) -> tuple[str, str, str] | None:
    fragment = sql_mapping.strip()
    if fragment.casefold().startswith("where "):
        fragment = fragment[6:].strip()
    try:
        expression = sqlglot.parse_one(fragment, read=datasource_dialect or "duckdb")
    except SqlglotError:
        return None
    if isinstance(expression, exp.EQ):
        return _column_literal_pair(expression.args.get("this"), expression.args.get("expression"))
    if isinstance(expression, exp.In) and len(expression.expressions) == 1:
        literal = expression.expressions[0]
        if isinstance(literal, exp.Literal) and literal.is_string:
            column = expression.this
            if isinstance(column, exp.Column):
                return column.table, column.name, str(literal.this)
    return None


def _column_literal_pair(left: object, right: object) -> tuple[str, str, str] | None:
    if isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_string:
        return left.table, left.name, str(right.this)
    if isinstance(right, exp.Column) and isinstance(left, exp.Literal) and left.is_string:
        return right.table, right.name, str(left.this)
    return None


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
        target_table=_optional_string(value.get("target_table"), ""),
        target_column=_optional_string(value.get("target_column"), ""),
        requested_value=_optional_string(value.get("requested_value"), ""),
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
            target_table=concept.target_table,
            target_column=concept.target_column,
            requested_value=concept.requested_value,
        )
        for index, concept in enumerate(concepts)
    )


def _concept_for_issue(
    issue: SemanticGroundingIssue,
    concepts: tuple[RequiredConcept, ...],
) -> RequiredConcept | None:
    by_id = {concept.concept_id: concept for concept in concepts}
    by_name = {concept.concept: concept for concept in concepts}
    return by_id.get(issue.concept_id) or by_name.get(issue.concept)


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


def _is_literal(value: object) -> TypeGuard[exp.Literal]:
    return isinstance(value, exp.Literal)


def _is_zero_literal(value: object) -> bool:
    return isinstance(value, exp.Literal) and not value.is_string and value.this == "0"
