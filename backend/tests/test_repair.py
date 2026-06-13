from backend.app.agent.repair import (
    iter_sql_repair_events,
    is_execution_repairable,
    is_guard_repairable,
    reset_failure_state,
)
from backend.app.agent.semantic_grounding import (
    ConceptExtractionRequest,
    ConceptExtractionResult,
    GroundingCheckRequest,
    GroundingCheckResult,
    RefutationAuditResult,
    RequiredConcept,
    SemanticGroundingIssue,
)
from backend.app.agent.schema_evidence import SchemaEvidence
from backend.app.agent.state import AgentState
from backend.app.core.llm_provider import SQLGenerationRequest, SQLGenerationResult
from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult
from backend.app.sql_guard.scope import GuardScope


def test_is_guard_repairable_accepts_repairable_stages():
    for stage in ("scope_guard", "syntax_guard", "function_guard", "fanout_guard", "cost_guard"):
        assert is_guard_repairable(GuardResult(allowed=False, stage=stage, reason="failed"))


def test_is_guard_repairable_rejects_operation_guard_and_missing_result():
    assert not is_guard_repairable(None)
    assert not is_guard_repairable(
        GuardResult(allowed=False, stage="operation_guard", reason="DELETE is not allowed.")
    )


def test_is_execution_repairable_accepts_duckdb_error_classes():
    class CatalogException(Exception):
        pass

    class BinderException(Exception):
        pass

    class ParserException(Exception):
        pass

    assert is_execution_repairable(CatalogException("Table does not exist"))
    assert is_execution_repairable(BinderException("Column not found"))
    assert is_execution_repairable(ParserException("syntax error"))
    assert is_execution_repairable(ValueError("Invalid Input Error: bad function argument"))


def test_is_execution_repairable_rejects_infrastructure_errors():
    assert not is_execution_repairable(TimeoutError("read operation timed out"))
    assert not is_execution_repairable(ConnectionError("connection refused"))
    assert not is_execution_repairable(MemoryError("out of memory"))


def test_reset_failure_state_clears_transient_failure_fields():
    state = AgentState(
        question="test",
        guard_result=GuardResult(allowed=False, stage="scope_guard", reason="bad column"),
        query_result=QueryResult(columns=["x"], rows=[[1]], row_count=1),
        summary="summary",
        explainability={"guard_result": {"allowed": False}},
        execution_error="CatalogException",
        error="bad column",
        stopped_at="sql_guard",
    )

    reset_failure_state(state)

    assert state.error is None
    assert state.stopped_at is None
    assert state.guard_result is None
    assert state.query_result is None
    assert state.summary is None
    assert state.explainability is None
    assert state.execution_error is None


def test_iter_sql_repair_events_repairs_guard_failure_and_backfills_history():
    state = AgentState(
        question="查询订单金额",
        schema_context="# Schema Context",
        sql="SELECT product_id FROM fact_orders",
    )
    provider = _ScriptedRepairProvider(["SELECT payment_amount FROM fact_orders"])

    events = list(
        iter_sql_repair_events(
            state,
            provider=provider,
            scope_builder=_scope,
            executor=lambda guard_result, datasource_name: QueryResult(
                columns=["payment_amount"],
                rows=[[100]],
                row_count=1,
            ),
        )
    )

    assert [event.step for event in events] == ["sql_guard", "repair_sql", "sql_guard", "execute"]
    assert state.error is None
    assert state.stopped_at is None
    assert state.query_result is not None
    assert state.query_result.row_count == 1
    assert provider.repair_requests[0].repair is not None
    assert provider.repair_requests[0].repair.error_stage == "sql_guard"
    assert provider.repair_requests[0].repair.error_kind == "scope_guard"
    assert state.repair_history == [
        {
            "attempt": 1,
            "original_sql": "SELECT product_id FROM fact_orders",
            "repaired_sql": "SELECT payment_amount FROM fact_orders",
            "error_stage": "sql_guard",
            "error_kind": "scope_guard",
            "error_reason": "Column product_id is not allowed.",
            "normalized_sql": None,
            "succeeded": True,
            "final_stage": "execute",
        }
    ]


def test_iter_sql_repair_events_repairs_execution_failure_and_backfills_history():
    class CatalogException(Exception):
        pass

    state = AgentState(
        question="查询订单金额",
        schema_context="# Schema Context",
        sql="SELECT payment_amount FROM fact_orders",
    )
    provider = _ScriptedRepairProvider(["SELECT order_id FROM fact_orders"])
    calls = []

    def executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        calls.append(guard_result.normalized_sql)
        if len(calls) == 1:
            raise CatalogException("Catalog Error: Column does not exist")
        return QueryResult(columns=["order_id"], rows=[["O1"]], row_count=1)

    events = list(
        iter_sql_repair_events(
            state,
            provider=provider,
            scope_builder=_scope,
            executor=executor,
        )
    )

    assert [event.step for event in events] == ["sql_guard", "repair_sql", "sql_guard", "execute"]
    assert len(calls) == 2
    assert state.execution_error is None
    assert provider.repair_requests[0].repair is not None
    assert provider.repair_requests[0].repair.error_stage == "execute"
    assert provider.repair_requests[0].repair.error_kind == "CatalogException"
    assert state.repair_history[0]["succeeded"] is True
    assert state.repair_history[0]["final_stage"] == "execute"


def test_iter_sql_repair_events_marks_unrepairable_execution_failure_terminal():
    state = AgentState(
        question="查询订单金额",
        schema_context="# Schema Context",
        sql="SELECT payment_amount FROM fact_orders",
    )
    provider = _ScriptedRepairProvider(["SELECT order_id FROM fact_orders"])

    def executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        raise TimeoutError("read operation timed out")

    events = list(
        iter_sql_repair_events(
            state,
            provider=provider,
            scope_builder=_scope,
            executor=executor,
        )
    )

    assert [event.step for event in events] == ["sql_guard", "error"]
    assert events[-1].error_stage == "execute"
    assert events[-1].error_reason == "read operation timed out"
    assert state.stopped_at == "execute"
    assert state.error == "read operation timed out"
    assert state.execution_error == "read operation timed out"
    assert state.query_result is None
    assert provider.repair_requests == []


def test_iter_sql_repair_events_does_not_repair_operation_guard():
    state = AgentState(
        question="查询订单",
        schema_context="# Schema Context",
        sql="DELETE FROM fact_orders",
    )
    provider = _ScriptedRepairProvider(["SELECT order_id FROM fact_orders"])

    events = list(
        iter_sql_repair_events(
            state,
            provider=provider,
            scope_builder=_scope,
            executor=lambda guard_result, datasource_name: QueryResult(columns=[], rows=[], row_count=0),
        )
    )

    assert [event.step for event in events] == ["sql_guard", "error"]
    assert events[-1].error_stage == "sql_guard"
    assert events[-1].error_kind == "operation_guard"
    assert state.repair_history == []
    assert provider.repair_requests == []


def test_iter_sql_repair_events_guards_repaired_candidate_and_caches_concepts():
    state = AgentState(
        question="删除的订单",
        schema_context="# Schema Context",
        sql="SELECT bad_column FROM fact_orders",
    )
    provider = _ScriptedRepairProvider(["SELECT order_id FROM fact_orders"])
    verifier = _ScriptedSemanticVerifier(
        checks=[
            GroundingCheckResult(
                ok=False,
                issues=(SemanticGroundingIssue(concept="删除的订单", failure_kind="omitted"),),
            )
        ]
    )

    events = list(
        iter_sql_repair_events(
            state,
            provider=provider,
            scope_builder=_scope,
            executor=lambda guard_result, datasource_name: QueryResult(
                columns=["order_id"],
                rows=[["O1"]],
                row_count=1,
            ),
            semantic_verifier=verifier,
            semantic_auditor=_SemanticAuditor(),
            semantic_mode="warn",
        )
    )

    assert [event.step for event in events] == ["sql_guard", "repair_sql", "sql_guard", "semantic_guard", "execute"]
    assert verifier.extraction_calls == 1
    assert verifier.check_calls == 1
    assert state.grounding_warnings[0]["concept"] == "删除的订单"
    assert state.repair_history[0]["succeeded"] is True
    assert state.repair_history[0]["final_stage"] == "execute"


def test_iter_sql_repair_events_reuses_semantic_extraction_after_execution_repair():
    class CatalogException(Exception):
        pass

    state = AgentState(
        question="删除的订单",
        schema_context="# Schema Context",
        sql="SELECT order_id FROM fact_orders",
    )
    provider = _ScriptedRepairProvider(["SELECT order_id FROM fact_orders LIMIT 20"])
    verifier = _ScriptedSemanticVerifier(
        checks=[
            GroundingCheckResult(
                ok=False,
                issues=(SemanticGroundingIssue(concept="删除的订单", failure_kind="omitted"),),
            ),
            GroundingCheckResult(
                ok=False,
                issues=(SemanticGroundingIssue(concept="删除的订单", failure_kind="omitted"),),
            ),
        ]
    )
    calls = []

    def executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        calls.append(guard_result.normalized_sql)
        if len(calls) == 1:
            raise CatalogException("Catalog Error: table missing")
        return QueryResult(columns=["order_id"], rows=[["O1"]], row_count=1)

    events = list(
        iter_sql_repair_events(
            state,
            provider=provider,
            scope_builder=_scope,
            executor=executor,
            semantic_verifier=verifier,
            semantic_auditor=_SemanticAuditor(),
            semantic_mode="warn",
        )
    )

    assert [event.step for event in events] == [
        "sql_guard",
        "semantic_guard",
        "repair_sql",
        "sql_guard",
        "semantic_guard",
        "execute",
    ]
    assert verifier.extraction_calls == 1
    assert verifier.check_calls == 2


def test_iter_sql_repair_events_stops_after_max_repair_attempts():
    state = AgentState(
        question="查询订单",
        schema_context="# Schema Context",
        sql="SELECT bad_column FROM fact_orders",
    )
    provider = _ScriptedRepairProvider(
        [
            "SELECT still_bad FROM fact_orders",
            "SELECT also_bad FROM fact_orders",
        ]
    )

    events = list(
        iter_sql_repair_events(
            state,
            provider=provider,
            scope_builder=_scope,
            executor=lambda guard_result, datasource_name: QueryResult(columns=[], rows=[], row_count=0),
        )
    )

    assert [event.step for event in events] == [
        "sql_guard",
        "repair_sql",
        "sql_guard",
        "repair_sql",
        "sql_guard",
        "error",
    ]
    assert len(state.repair_history) == 2
    assert state.repair_history[0]["succeeded"] is False
    assert state.repair_history[0]["final_stage"] == "scope_guard"
    assert state.repair_history[1]["succeeded"] is False
    assert state.repair_history[1]["final_stage"] == "scope_guard"


class _ScriptedRepairProvider:
    name = "scripted"

    def __init__(self, repair_sqls: list[str]) -> None:
        self._repair_sqls = list(repair_sqls)
        self.repair_requests: list[SQLGenerationRequest] = []

    def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
        self.repair_requests.append(request)
        if not self._repair_sqls:
            raise AssertionError("No scripted repair SQL left.")
        return SQLGenerationResult(sql=self._repair_sqls.pop(0), provider=self.name)


class _ScriptedSemanticVerifier:
    def __init__(self, checks: list[GroundingCheckResult]) -> None:
        self._checks = list(checks)
        self.extraction_calls = 0
        self.check_calls = 0

    def extract_required_concepts(self, request: ConceptExtractionRequest) -> ConceptExtractionResult:
        self.extraction_calls += 1
        return ConceptExtractionResult(
            concepts=(RequiredConcept(concept="删除的订单", concept_type="filter", supported=False),)
        )

    def check_grounding(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        self.check_calls += 1
        if not self._checks:
            raise AssertionError("No scripted semantic checks left.")
        return self._checks.pop(0)


class _SemanticAuditor:
    def full_schema_context(self, *, datasource_name: str) -> str:
        return "# Full Schema Context"

    def evidence(self, *, datasource_name: str) -> SchemaEvidence:
        return SchemaEvidence(datasource_name=datasource_name)

    def audit(
        self,
        issue: SemanticGroundingIssue,
        *,
        evidence: SchemaEvidence,
        concept: RequiredConcept | None = None,
    ) -> RefutationAuditResult:
        return RefutationAuditResult(confirmed=True, reason="No evidence.")


def _scope(datasource_name: str = "duckdb_ecommerce") -> GuardScope:
    del datasource_name
    return GuardScope(
        allowed_tables=frozenset({"fact_orders"}),
        table_columns={
            "fact_orders": frozenset({"order_id", "payment_amount"}),
        },
    )
