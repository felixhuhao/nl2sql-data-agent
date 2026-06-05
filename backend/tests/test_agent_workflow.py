import pytest

import backend.app.agent.nodes as nodes_module
from backend.app.agent.conversation import ConversationContext, FilterPredicate, build_conversation_context
from backend.app.agent.nodes import (
    build_context_node,
    datasource_selected_node,
    execute_node,
    generate_sql_node,
    intent_guard_node,
    iter_pre_repair_workflow,
    olap_intent_detect_node,
    repair_sql_node,
    retrieve_context_node,
    sql_guard_node,
)
from backend.app.agent.performance import explain_performance_node
from backend.app.agent.repair import iter_sql_repair_events
from backend.app.agent.state import AgentState
from backend.app.agent.workflow import run_query_workflow
from backend.app.core.llm_provider import (
    MockLLMProvider,
    SQLGenerationRequest,
    SQLGenerationResult,
    SQLRepairContext,
)
from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult
from backend.app.sql_guard.scope import GuardScope


def test_retrieve_context_node_sets_result_and_step():
    state = AgentState(question="test")

    retrieve_context_node(
        state,
        retriever=lambda question, datasource_name: {
            "question": question,
            "datasource": datasource_name,
            "tables": [],
        },
    )

    assert state.retrieval_result == {"question": "test", "datasource": "duckdb_ecommerce", "tables": []}
    assert state.completed_steps == ["retrieve_context"]


def test_datasource_selected_node_sets_datasource_metadata(monkeypatch):
    class FakeConnector:
        name = "clickhouse_ecommerce"
        dialect = "clickhouse"
        display_name = "ClickHouse (OLAP)"

    class FakeManager:
        def get(self, datasource_name: str):
            assert datasource_name == "clickhouse_ecommerce"
            return FakeConnector()

    monkeypatch.setattr(nodes_module, "get_datasource_manager", lambda: FakeManager())
    state = AgentState(question="test", datasource_name="clickhouse_ecommerce")

    datasource_selected_node(state)

    assert state.datasource_name == "clickhouse_ecommerce"
    assert state.datasource_dialect == "clickhouse"
    assert state.datasource_display_name == "ClickHouse (OLAP)"
    assert state.completed_steps == ["datasource_selected"]


@pytest.mark.parametrize(
    ("question", "expected_error"),
    [
        ("删除2024年的订单数据", "DELETE intent is not allowed."),
        ("从外部 CSV 读取订单数据", "EXTERNAL_FILE_READ intent is not allowed."),
    ],
)
def test_intent_guard_node_rejects_blocked_questions(question: str, expected_error: str):
    state = AgentState(question=question)

    intent_guard_node(state)

    assert state.stopped_at == "intent_guard"
    assert state.error == expected_error
    assert state.completed_steps == ["intent_guard"]


def test_build_context_node_sets_context_and_step():
    state = AgentState(question="test")

    build_context_node(state, schema_context_builder=lambda: "# Context")

    assert state.schema_context == "# Context"
    assert state.completed_steps == ["build_context"]


def test_build_context_node_uses_retrieval_result(monkeypatch):
    state = AgentState(question="test", retrieval_result={"fallback_used": False})
    monkeypatch.setattr(
        nodes_module,
        "build_focused_context_from_retrieval",
        lambda retrieval_result, datasource_name: f"# Focused {datasource_name} {retrieval_result['fallback_used']}",
    )

    build_context_node(state)

    assert state.schema_context == "# Focused duckdb_ecommerce False"
    assert state.completed_steps == ["build_context"]


def test_build_context_node_unions_prior_assets(monkeypatch):
    captured = {}

    def fake_build_focused_context_from_retrieval(retrieval_result, datasource_name):
        captured["retrieval_result"] = retrieval_result
        captured["datasource_name"] = datasource_name
        return "# Focused Context"

    monkeypatch.setattr(nodes_module, "build_focused_context_from_retrieval", fake_build_focused_context_from_retrieval)
    state = AgentState(
        question="改成最近90天",
        retrieval_result={"fallback_used": False, "tables": [], "columns": []},
        conversation_context=ConversationContext(
            question="查询最近30天销售额",
            normalized_sql="SELECT SUM(payment_amount) AS sales_amount FROM fact_orders",
            datasource_name="duckdb_ecommerce",
            matched_tables=["fact_orders"],
            matched_columns=["fact_orders.payment_amount"],
        ),
    )

    build_context_node(state)

    assert state.schema_context == "# Focused Context"
    assert captured["datasource_name"] == "duckdb_ecommerce"
    assert captured["retrieval_result"]["tables"] == [
        {"table_name": "fact_orders", "source": "conversation_context"}
    ]
    assert captured["retrieval_result"]["columns"] == [
        {
            "table_name": "fact_orders",
            "column_name": "payment_amount",
            "source": "conversation_context",
        }
    ]


def test_build_context_node_retrieves_when_missing_retrieval_result(monkeypatch):
    state = AgentState(question="test")
    monkeypatch.setattr(
        nodes_module,
        "build_focused_context",
        lambda question, datasource_name: f"# Focused {datasource_name} {question}",
    )

    build_context_node(state)

    assert state.schema_context == "# Focused duckdb_ecommerce test"
    assert state.completed_steps == ["build_context"]


def test_olap_intent_detect_node_sets_intents_and_step():
    state = AgentState(
        question="查询销售额前10的商品同比增长",
        datasource_dialect="clickhouse",
        retrieval_result={"metrics": [{"name": "sales_amount"}]},
    )

    olap_intent_detect_node(state)

    assert state.olap_intents == ["topn", "yoy_mom"]
    assert "sales_amount" in state.olap_hint
    assert "TopN / ranking SQL guidance" in state.olap_hint
    assert "YoY / MoM SQL guidance" in state.olap_hint
    assert state.completed_steps == ["olap_detected"]


def test_iter_pre_repair_workflow_runs_shared_step_sequence(monkeypatch):
    class SelectProvider:
        name = "select-provider"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            return SQLGenerationResult(
                sql="SELECT payment_amount FROM fact_orders",
                provider=self.name,
            )

    monkeypatch.setattr(
        nodes_module,
        "build_focused_context_from_retrieval",
        lambda retrieval_result, datasource_name: "# Focused Context",
    )
    state = AgentState(question="查询销售额前10的商品同比增长")

    steps = list(
        iter_pre_repair_workflow(
            state,
            provider=SelectProvider(),
            retriever=lambda question, datasource_name: {
                "question": question,
                "datasource": datasource_name,
                "metrics": [{"name": "sales_amount"}],
            },
        )
    )

    assert steps == [
        "datasource_selected",
        "intent_guard",
        "retrieve_context",
        "build_context",
        "olap_detected",
        "generate_sql",
    ]
    assert state.completed_steps == steps
    assert state.olap_intents == ["topn", "yoy_mom"]
    assert state.sql == "SELECT payment_amount FROM fact_orders"


def test_generate_sql_node_passes_olap_context_to_provider():
    captured_requests = []

    class CapturingProvider:
        name = "capturing"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            captured_requests.append(request)
            return SQLGenerationResult(
                sql="SELECT payment_amount FROM fact_orders",
                provider=self.name,
            )

    state = AgentState(
        question="查询销售额前10的商品同比增长",
        schema_context="# Schema Context",
        olap_intents=["topn", "yoy_mom"],
        olap_hint="TopN guidance",
    )

    generate_sql_node(state, provider=CapturingProvider())

    assert captured_requests[0].olap_intents == ["topn", "yoy_mom"]
    assert captured_requests[0].olap_hint == "TopN guidance"


def test_generate_sql_node_requires_schema_context():
    state = AgentState(question="test")

    with pytest.raises(ValueError, match="schema_context"):
        generate_sql_node(state, provider=MockLLMProvider())


def test_generate_sql_node_normalizes_metric_alias():
    class MetricProvider:
        name = "metric-provider"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            return SQLGenerationResult(
                sql="SELECT SUM(fact_orders.payment_amount) FROM fact_orders",
                provider=self.name,
            )

    state = AgentState(question="营收总额是多少", schema_context="# Schema Context")

    generate_sql_node(state, provider=MetricProvider())

    assert state.sql == "SELECT SUM(fact_orders.payment_amount) AS sales_amount FROM fact_orders"
    assert state.provider == "metric-provider"


def test_repair_sql_node_records_repair_history():
    captured_requests = []

    class RepairProvider:
        name = "repair-provider"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            captured_requests.append(request)
            return SQLGenerationResult(
                sql="SELECT payment_amount FROM fact_orders",
                provider=self.name,
            )

    repair_context = SQLRepairContext(
        attempt=1,
        original_sql="SELECT product_id FROM fact_orders",
        error_stage="sql_guard",
        error_kind="scope_guard",
        error_reason="Column fact_orders.product_id is not allowed.",
    )
    state = AgentState(
        question="按类目统计销售额",
        schema_context="# Schema Context",
        sql=repair_context.original_sql,
    )

    repair_sql_node(state, provider=RepairProvider(), repair_context=repair_context)

    assert captured_requests == [
        SQLGenerationRequest(
            question="按类目统计销售额",
            schema_context="# Schema Context",
            repair=repair_context,
        )
    ]
    assert state.sql == "SELECT payment_amount FROM fact_orders"
    assert state.provider == "repair-provider"
    assert state.completed_steps == ["repair_sql"]
    assert state.repair_history == [
        {
            "attempt": 1,
            "original_sql": "SELECT product_id FROM fact_orders",
            "repaired_sql": "SELECT payment_amount FROM fact_orders",
            "error_stage": "sql_guard",
            "error_kind": "scope_guard",
            "error_reason": "Column fact_orders.product_id is not allowed.",
            "normalized_sql": None,
            "succeeded": None,
            "final_stage": None,
        }
    ]


def test_conversation_filter_verify_stops_before_execute_when_filter_missing():
    class Provider:
        name = "provider"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            raise AssertionError("repair should not run when max_repairs=0")

    def executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        raise AssertionError("unverified SQL must not execute")

    state = AgentState(
        question="换成订单数",
        schema_context="# Schema Context",
        sql="SELECT COUNT(*) AS order_count FROM fact_orders",
        conversation_context=ConversationContext(
            question="只看华东",
            normalized_sql=(
                "SELECT SUM(fact_orders.payment_amount) AS sales_amount "
                "FROM fact_orders JOIN dim_regions ON fact_orders.region_key = dim_regions.region_key "
                "WHERE dim_regions.region_group = '华东'"
            ),
            datasource_name="duckdb_ecommerce",
            active_filters=[
                FilterPredicate(column="dim_regions.region_group", op="=", value="华东"),
            ],
        ),
        is_follow_up=True,
        change_kind="metric",
    )

    events = list(
        iter_sql_repair_events(
            state,
            provider=Provider(),
            scope_builder=lambda datasource_name: GuardScope(
                allowed_tables=frozenset({"fact_orders", "dim_regions"}),
                table_columns={
                    "fact_orders": frozenset({"payment_amount", "region_key"}),
                    "dim_regions": frozenset({"region_key", "region_group"}),
                },
            ),
            executor=executor,
            max_repairs=0,
        )
    )

    assert [event.step for event in events] == ["sql_guard", "conversation_filter_verify", "error"]
    assert state.stopped_at == "conversation_filter_verify"
    assert "dim_regions.region_group = 华东" in state.error


def test_build_conversation_context_captures_filter_followup_from_sql_diff_without_value_hits():
    state = AgentState(
        question="只看华东",
        datasource_dialect="duckdb",
        conversation_context=ConversationContext(
            question="查询最近30天销售额",
            normalized_sql="SELECT SUM(fact_orders.payment_amount) AS sales_amount FROM fact_orders",
            datasource_name="duckdb_ecommerce",
        ),
        is_follow_up=True,
        change_kind="filter",
        retrieval_result={"retrieval_meta": {"value_hits": []}},
        guard_result=GuardResult(
            allowed=True,
            stage="passed",
            normalized_sql=(
                "SELECT SUM(fact_orders.payment_amount) AS sales_amount "
                "FROM fact_orders "
                "JOIN dim_regions ON fact_orders.region_key = dim_regions.region_key "
                "WHERE dim_regions.region_group = '华东'"
            ),
        ),
        query_result=QueryResult(columns=["sales_amount"], rows=[[100]], row_count=1),
        explainability={
            "matched_tables": ["fact_orders", "dim_regions"],
            "matched_columns": ["fact_orders.payment_amount", "dim_regions.region_group"],
            "join_paths": [{"source_table": "fact_orders", "target_table": "dim_regions"}],
        },
    )

    context = build_conversation_context(state)

    assert context is not None
    assert context.active_filters == [
        FilterPredicate(column="dim_regions.region_group", op="=", value="华东")
    ]
    assert context.join_paths == [{"source_table": "fact_orders", "target_table": "dim_regions"}]


def test_build_conversation_context_drops_prior_filters_for_non_followup_turn():
    state = AgentState(
        question="查询所有渠道销售额",
        datasource_dialect="duckdb",
        conversation_context=ConversationContext(
            question="只看华东",
            normalized_sql="SELECT 1",
            datasource_name="duckdb_ecommerce",
            active_filters=[
                FilterPredicate(column="dim_regions.region_group", op="=", value="华东")
            ],
        ),
        is_follow_up=False,
        change_kind="none",
        guard_result=GuardResult(
            allowed=True,
            stage="passed",
            normalized_sql="SELECT dim_channels.channel_name FROM dim_channels",
        ),
        query_result=QueryResult(columns=["channel_name"], rows=[["官网"]], row_count=1),
        explainability={"matched_tables": ["dim_channels"], "matched_columns": ["dim_channels.channel_name"]},
    )

    context = build_conversation_context(state)

    assert context is not None
    assert context.active_filters == []


def test_build_conversation_context_replaces_prior_filter_when_filter_followup_changes_value():
    state = AgentState(
        question="改看华北",
        datasource_dialect="duckdb",
        conversation_context=ConversationContext(
            question="只看华东",
            normalized_sql=(
                "SELECT SUM(fact_orders.payment_amount) AS sales_amount "
                "FROM fact_orders JOIN dim_regions ON fact_orders.region_key = dim_regions.region_key "
                "WHERE dim_regions.region_group = '华东'"
            ),
            datasource_name="duckdb_ecommerce",
            active_filters=[
                FilterPredicate(column="dim_regions.region_group", op="=", value="华东")
            ],
        ),
        is_follow_up=True,
        change_kind="filter",
        guard_result=GuardResult(
            allowed=True,
            stage="passed",
            normalized_sql=(
                "SELECT SUM(fact_orders.payment_amount) AS sales_amount "
                "FROM fact_orders JOIN dim_regions ON fact_orders.region_key = dim_regions.region_key "
                "WHERE dim_regions.region_group = '华北'"
            ),
        ),
        query_result=QueryResult(columns=["sales_amount"], rows=[[100]], row_count=1),
        explainability={"matched_tables": ["fact_orders", "dim_regions"]},
    )

    context = build_conversation_context(state)

    assert context is not None
    assert context.active_filters == [
        FilterPredicate(column="dim_regions.region_group", op="=", value="华北")
    ]


def test_repair_sql_node_deterministically_repairs_product_name_alias():
    class ShouldNotCallProvider:
        name = "unused"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            raise AssertionError("deterministic repair should not call provider")

    original_sql = """
SELECT p.product_name, SUM(fi.item_amount) AS sales_amount
FROM fact_order_items fi
JOIN dim_products p ON fi.product_key = p.product_key
GROUP BY p.product_name
ORDER BY sales_amount DESC
LIMIT 10
"""
    repair_context = SQLRepairContext(
        attempt=1,
        original_sql=original_sql,
        error_stage="sql_guard",
        error_kind="scope_guard",
        error_reason="Column dim_products.product_name is not allowed.",
    )
    state = AgentState(
        question="Show top products by sales",
        schema_context="# Schema Context",
        sql=original_sql,
    )

    repair_sql_node(state, provider=ShouldNotCallProvider(), repair_context=repair_context)

    assert "p.name AS product_name" in state.sql
    assert "GROUP BY p.name" in state.sql
    assert "p.product_name" not in state.sql
    assert "SELECT p.product_key" not in state.sql
    assert "GROUP BY p.product_key" not in state.sql
    assert state.provider == "deterministic-repair"
    assert state.repair_history[0]["repaired_sql"] == state.sql


def test_repair_sql_node_deterministic_product_name_repair_does_not_depend_on_error_text():
    class ShouldNotCallProvider:
        name = "unused"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            raise AssertionError("deterministic repair should not call provider")

    original_sql = """
SELECT p.product_name, SUM(fi.item_amount) AS sales_amount
FROM fact_order_items fi
JOIN dim_products p ON fi.product_key = p.product_key
GROUP BY p.product_name
ORDER BY sales_amount DESC
LIMIT 10
"""
    repair_context = SQLRepairContext(
        attempt=1,
        original_sql=original_sql,
        error_stage="sql_guard",
        error_kind="scope_guard",
        error_reason="Selected column is outside the allowed scope.",
    )
    state = AgentState(
        question="Show top products by sales",
        schema_context="# Schema Context",
        sql=original_sql,
    )

    repair_sql_node(state, provider=ShouldNotCallProvider(), repair_context=repair_context)

    assert "p.name AS product_name" in state.sql
    assert "p.product_name" not in state.sql
    assert state.provider == "deterministic-repair"


def test_sql_guard_node_requires_sql():
    state = AgentState(question="test")

    with pytest.raises(ValueError, match="sql is required"):
        sql_guard_node(state, scope_builder=_scope)


def test_execute_node_requires_guard_result():
    state = AgentState(question="test")

    with pytest.raises(ValueError, match="guard_result"):
        execute_node(state)


def test_execute_node_skips_rejected_guard_result_without_overwriting_error():
    state = AgentState(
        question="test",
        guard_result=GuardResult(allowed=False, stage="operation_guard", reason="DROP is not allowed."),
        error="existing error",
        stopped_at="sql_guard",
    )

    execute_node(state)

    assert state.error == "existing error"
    assert state.stopped_at == "sql_guard"
    assert state.query_result is None
    assert state.completed_steps == []


def test_explain_performance_node_skips_non_clickhouse_datasource():
    state = AgentState(
        question="test",
        datasource_dialect="duckdb",
        query_result=QueryResult(columns=["x"], rows=[[1]], row_count=1, elapsed_ms=3.2),
    )

    explain_performance_node(state)

    assert state.plan_hints == []
    assert state.runtime_stats is None
    assert state.completed_steps == []


def test_explain_performance_node_skips_failed_execution(monkeypatch):
    class FailingManager:
        def get(self, datasource_name: str):
            raise AssertionError("EXPLAIN should not be requested")

    monkeypatch.setattr("backend.app.agent.performance.get_datasource_manager", lambda: FailingManager())
    state = AgentState(
        question="test",
        datasource_name="clickhouse_ecommerce",
        datasource_dialect="clickhouse",
        guard_result=GuardResult(allowed=True, stage="passed", normalized_sql="SELECT 1"),
        execution_error="boom",
    )

    explain_performance_node(state)

    assert state.plan_hints == []
    assert state.runtime_stats is None
    assert state.completed_steps == []


def test_explain_performance_node_sets_clickhouse_plan_hints(monkeypatch):
    class FakeConnector:
        def explain(self, sql: str):
            assert sql == "SELECT * FROM fact_orders"
            return {
                "lines": [
                    "ReadFromMergeTree Parts: 3/24 SortingKey",
                    "JoiningTransform",
                ]
            }

    class FakeManager:
        def get(self, datasource_name: str):
            assert datasource_name == "clickhouse_ecommerce"
            return FakeConnector()

    monkeypatch.setattr("backend.app.agent.performance.get_datasource_manager", lambda: FakeManager())
    state = AgentState(
        question="test",
        datasource_name="clickhouse_ecommerce",
        datasource_dialect="clickhouse",
        guard_result=GuardResult(allowed=True, stage="passed", normalized_sql="SELECT * FROM fact_orders"),
        query_result=QueryResult(columns=["x"], rows=[[1]], row_count=1, elapsed_ms=12.5),
        explainability={"matched_tables": ["fact_orders"]},
    )

    explain_performance_node(state)

    assert state.plan_hints == [
        "命中分区裁剪，扫描 3/24 parts。",
        "查询计划包含排序键相关读取。",
        "包含 1 个 JOIN。",
        "建议添加明确的时间范围过滤以减少 ClickHouse 扫描。",
    ]
    assert state.runtime_stats == {"execution_time_ms": 12.5}
    assert state.completed_steps == ["explain_plan"]


def test_run_query_workflow_executes_demo_question():
    executed_sql = []

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        assert datasource_name == "duckdb_ecommerce"
        executed_sql.append(guard_result.normalized_sql)
        return QueryResult(
            columns=["date_value", "sales_amount", "order_count"],
            rows=[["2025-12-31", 100, 2]],
            row_count=1,
        )

    state = run_query_workflow(
        "查询最近30天每日销售额和订单数",
        schema_context_builder=lambda: "# Schema Context",
        scope_builder=_scope,
        executor=fake_executor,
    )

    assert state.stopped_at is None
    assert state.error is None
    assert state.provider == "mock"
    assert state.matched_query_id == "recent_30d_daily_sales"
    assert state.guard_result is not None
    assert state.guard_result.allowed is True
    assert state.explainability is not None
    assert state.explainability["matched_tables"] == ["dim_date", "fact_orders"]
    assert "fact_orders.payment_amount" in state.explainability["matched_columns"]
    assert state.explainability["date_interpretation"]["matched"] is True
    assert state.explainability["guard_result"]["allowed"] is True
    assert state.query_result is not None
    assert state.query_result.row_count == 1
    assert state.summary == "查询返回 1 行，字段：date_value, sales_amount, order_count。"
    assert state.completed_steps == [
        "datasource_selected",
        "intent_guard",
        "build_context",
        "olap_detected",
        "generate_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]
    assert executed_sql
    assert "LIMIT 500" in executed_sql[0]


def test_run_query_workflow_default_uses_retrieval_and_focused_context(monkeypatch):
    captured_schema_context = []

    class CapturingProvider:
        name = "capturing"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            captured_schema_context.append(request.schema_context)
            return SQLGenerationResult(
                sql="SELECT order_id FROM fact_orders",
                provider=self.name,
            )

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        assert datasource_name == "duckdb_ecommerce"
        return QueryResult(columns=["order_id"], rows=[["O1"]], row_count=1)

    monkeypatch.setattr(
        nodes_module,
        "build_focused_context_from_retrieval",
        lambda retrieval_result, datasource_name: f"# Focused Context {datasource_name}",
    )

    state = run_query_workflow(
        "查询订单",
        provider=CapturingProvider(),
        retriever=lambda question, datasource_name: {
            "question": question,
            "datasource": datasource_name,
            "fallback_used": False,
        },
        scope_builder=_scope,
        executor=fake_executor,
    )

    assert state.retrieval_result == {
        "question": "查询订单",
        "datasource": "duckdb_ecommerce",
        "fallback_used": False,
    }
    assert captured_schema_context == ["# Focused Context duckdb_ecommerce"]
    assert state.completed_steps == [
        "datasource_selected",
        "intent_guard",
        "retrieve_context",
        "build_context",
        "olap_detected",
        "generate_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]


def test_run_query_workflow_repairs_sql_then_finalizes():
    class RepairingProvider:
        name = "repairing"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            if request.repair is not None:
                return SQLGenerationResult(
                    sql="SELECT payment_amount FROM fact_orders",
                    provider=self.name,
                )
            return SQLGenerationResult(
                sql="SELECT missing_column FROM fact_orders",
                provider=self.name,
            )

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        assert datasource_name == "duckdb_ecommerce"
        assert guard_result.normalized_sql is not None
        assert "payment_amount" in guard_result.normalized_sql
        return QueryResult(columns=["payment_amount"], rows=[[100]], row_count=1)

    state = run_query_workflow(
        "查询订单金额",
        provider=RepairingProvider(),
        schema_context_builder=lambda: "# Schema Context",
        scope_builder=_scope,
        executor=fake_executor,
    )

    assert state.stopped_at is None
    assert state.error is None
    assert state.query_result is not None
    assert state.summary == "查询返回 1 行，字段：payment_amount。"
    assert state.chart_recommendation is not None
    assert state.repair_history == [
        {
            "attempt": 1,
            "error_stage": "sql_guard",
            "error_kind": "scope_guard",
            "error_reason": "Column missing_column is not allowed.",
            "original_sql": "SELECT missing_column FROM fact_orders",
            "normalized_sql": None,
            "repaired_sql": "SELECT payment_amount FROM fact_orders",
            "succeeded": True,
            "final_stage": "execute",
        }
    ]
    assert state.completed_steps == [
        "datasource_selected",
        "intent_guard",
        "build_context",
        "olap_detected",
        "generate_sql",
        "sql_guard",
        "repair_sql",
        "sql_guard",
        "execute",
        "summarize",
        "recommend_chart",
    ]


def test_run_query_workflow_stops_when_intent_guard_rejects_question():
    class FailingProvider:
        name = "failing"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            raise AssertionError("provider should not be called")

    def failing_retriever(question: str) -> dict:
        raise AssertionError("retriever should not be called")

    state = run_query_workflow(
        "删除2024年的订单数据",
        provider=FailingProvider(),
        retriever=failing_retriever,
        scope_builder=_scope,
    )

    assert state.stopped_at == "intent_guard"
    assert state.error == "DELETE intent is not allowed."
    assert state.sql is None
    assert state.guard_result is None
    assert state.query_result is None
    assert state.completed_steps == ["datasource_selected", "intent_guard"]


def test_run_query_workflow_stops_when_guard_rejects_sql():
    class DeleteProvider:
        name = "delete-provider"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            return SQLGenerationResult(sql="DELETE FROM fact_orders", provider=self.name)

    executed = []

    def fake_executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        executed.append(guard_result)
        return QueryResult(columns=[], rows=[], row_count=0)

    state = run_query_workflow(
        "查询订单",
        provider=DeleteProvider(),
        schema_context_builder=lambda: "# Schema Context",
        scope_builder=_scope,
        executor=fake_executor,
    )

    assert state.stopped_at == "sql_guard"
    assert state.error == "DELETE is not allowed."
    assert state.guard_result is not None
    assert state.guard_result.allowed is False
    assert state.explainability is not None
    assert state.explainability["guard_result"]["allowed"] is False
    assert state.explainability["guard_result"]["stage"] == "operation_guard"
    assert state.query_result is None
    assert executed == []
    assert state.completed_steps == [
        "datasource_selected",
        "intent_guard",
        "build_context",
        "olap_detected",
        "generate_sql",
        "sql_guard",
    ]


def test_run_query_workflow_passes_clickhouse_datasource_through_chain(monkeypatch):
    captured = {}

    class FakeConnector:
        name = "clickhouse_ecommerce"
        dialect = "clickhouse"
        display_name = "ClickHouse (OLAP)"

    class FakeManager:
        def get(self, datasource_name: str):
            assert datasource_name == "clickhouse_ecommerce"
            return FakeConnector()

    class CapturingProvider:
        name = "capturing"

        def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResult:
            captured["request_datasource"] = request.datasource_name
            captured["request_dialect"] = request.datasource_dialect
            return SQLGenerationResult(
                sql="SELECT toStartOfMonth(dim_date.date_value) AS month FROM dim_date LIMIT 1",
                provider=self.name,
            )

    def retriever(question: str, datasource_name: str) -> dict:
        captured["retriever_datasource"] = datasource_name
        return {"question": question, "fallback_used": False}

    def executor(guard_result: GuardResult, datasource_name: str) -> QueryResult:
        captured["executor_datasource"] = datasource_name
        return QueryResult(columns=["month"], rows=[["2025-12-01"]], row_count=1)

    monkeypatch.setattr(nodes_module, "get_datasource_manager", lambda: FakeManager())
    monkeypatch.setattr(
        nodes_module,
        "build_focused_context_from_retrieval",
        lambda retrieval_result, datasource_name: "# ClickHouse Context",
    )

    state = run_query_workflow(
        "按月统计",
        provider=CapturingProvider(),
        retriever=retriever,
        scope_builder=_scope,
        executor=executor,
        datasource_name="clickhouse_ecommerce",
    )

    assert state.datasource_dialect == "clickhouse"
    assert state.datasource_display_name == "ClickHouse (OLAP)"
    assert captured == {
        "retriever_datasource": "clickhouse_ecommerce",
        "request_datasource": "clickhouse_ecommerce",
        "request_dialect": "clickhouse",
        "executor_datasource": "clickhouse_ecommerce",
    }


def _scope(datasource_name: str = "duckdb_ecommerce") -> GuardScope:
    del datasource_name
    return GuardScope(
        allowed_tables=frozenset({"fact_orders", "dim_date"}),
        table_columns={
            "fact_orders": frozenset({"order_id", "date_key", "payment_amount"}),
            "dim_date": frozenset({"date_key", "date_value"}),
        },
    )
