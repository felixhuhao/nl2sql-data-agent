from dataclasses import dataclass, field

from backend.app.execution.runner import QueryResult
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.sql_guard.models import GuardResult


@dataclass
class AgentState:
    question: str
    datasource_name: str = DEFAULT_DATASOURCE
    datasource_dialect: str = "duckdb"
    datasource_display_name: str = "DuckDB (本地)"
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
    olap_intents: list[str] = field(default_factory=list)
    olap_hint: str = ""
    plan_hints: list[str] = field(default_factory=list)
    runtime_stats: dict | None = None
    error: str | None = None
    stopped_at: str | None = None
    repair_history: list[dict] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
