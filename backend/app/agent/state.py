from dataclasses import dataclass, field

from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult


@dataclass
class AgentState:
    question: str
    schema_context: str | None = None
    sql: str | None = None
    provider: str | None = None
    matched_query_id: str | None = None
    guard_result: GuardResult | None = None
    query_result: QueryResult | None = None
    summary: str | None = None
    explainability: dict | None = None
    error: str | None = None
    stopped_at: str | None = None
    completed_steps: list[str] = field(default_factory=list)
