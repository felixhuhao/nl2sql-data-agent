from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

from backend.app.execution.runner import QueryResult
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.sql_guard.models import GuardResult
from backend.app.visualization.recommender import ChartRecommendation

if TYPE_CHECKING:
    from backend.app.agent.conversation import ConversationContext, FilterPredicate
    from backend.app.agent.schema_evidence import SchemaEvidence


class SemanticGuardResult(TypedDict, total=False):
    ok: bool | None
    verifier_unavailable: bool
    reason: str
    issues: list[dict]
    sql_facts: dict


class GroundingWarningPayload(TypedDict, total=False):
    concept: str
    concept_id: str
    concept_type: str
    failure_kind: str
    sql_mapping: str | None
    supported: bool
    explanation: str
    refutation_confirmed: bool
    refutation_reason: str
    message: str


@dataclass
class AgentState:
    question: str
    datasource_name: str = DEFAULT_DATASOURCE
    datasource_dialect: str = "duckdb"
    datasource_display_name: str = "DuckDB (本地)"
    retrieval_result: dict | None = None
    retrieval_coverage: dict | None = None
    schema_context: str | None = None
    sql: str | None = None
    provider: str | None = None
    matched_query_id: str | None = None
    guard_result: GuardResult | None = None
    query_result: QueryResult | None = None
    summary: str | None = None
    explainability: dict | None = None
    execution_error: str | None = None
    olap_intents: list[str] = field(default_factory=list)
    olap_hint: str = ""
    conversation_context: "ConversationContext | None" = None
    is_follow_up: bool = False
    change_kind: str = "none"
    missing_carried_filters: list["FilterPredicate"] = field(default_factory=list)
    plan_hints: list[str] = field(default_factory=list)
    runtime_stats: dict | None = None
    chart_recommendation: ChartRecommendation | None = None
    full_schema_context: str | None = None
    schema_evidence: "SchemaEvidence | None" = None
    required_concepts: list[dict] | None = None
    semantic_guard_result: SemanticGuardResult | None = None
    grounding_warnings: list[GroundingWarningPayload] = field(default_factory=list)
    error: str | None = None
    stopped_at: str | None = None
    repair_history: list[dict] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
