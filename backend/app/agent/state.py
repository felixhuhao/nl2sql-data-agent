from dataclasses import dataclass, field

from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult


@dataclass
class AgentState:
    question: str
    retrieval_result: dict | None = None
    schema_context: str | None = None
    sql: str | None = None
    provider: str | None = None
    matched_query_id: str | None = None
    guard_result: GuardResult | None = None
    query_result: QueryResult | None = None
    summary: str | None = None
    explainability: dict | None = None
    execution_error: str | None = None
    error: str | None = None
    stopped_at: str | None = None
    repair_history: list[dict] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
