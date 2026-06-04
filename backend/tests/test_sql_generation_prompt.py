from backend.app.agent.prompts.sql_generation import build_sql_generation_messages
from backend.app.core.llm_provider import SQLGenerationRequest, SQLRepairContext


def test_sql_generation_prompt_contains_core_constraints():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="查询最近30天每日销售额和订单数",
            schema_context="# Schema Context",
        )
    )

    system_prompt = messages[0]["content"]
    assert "Return SQL only" in system_prompt
    assert "DuckDB SQL dialect" in system_prompt
    assert "single SELECT statement" in system_prompt
    assert "Analysis Space" in system_prompt
    assert "Qualify every physical column" in system_prompt
    assert "Alias every computed projection" in system_prompt
    assert "use the metric name as the SELECT alias" in system_prompt
    assert "INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, COPY, INSTALL, or LOAD" in system_prompt
    assert "read_csv, read_json, or read_parquet" in system_prompt
    assert "SUM(fact_order_items.item_amount)" in system_prompt
    assert "Do not aggregate fact_orders.payment_amount after joining fact_order_items" in system_prompt


def test_sql_generation_prompt_includes_schema_context_and_question():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="查询订单",
            schema_context="# Schema Context",
        )
    )

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "# Schema Context" in messages[1]["content"]
    assert "查询订单" in messages[1]["content"]


def test_sql_generation_prompt_uses_multi_turn_repair_context():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="按品类统计销售额",
            schema_context="# Schema Context",
            repair=SQLRepairContext(
                attempt=1,
                original_sql="SELECT p.category, SUM(o.payment_amount) FROM fact_orders o",
                error_stage="sql_guard",
                error_kind="fanout_guard",
                error_reason="Joining fact_order_items can inflate sales amount.",
                normalized_sql="SELECT p.category, SUM(o.payment_amount) FROM fact_orders AS o",
            ),
        )
    )

    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[2]["content"] == "SELECT p.category, SUM(o.payment_amount) FROM fact_orders o"
    repair_message = messages[3]["content"]
    assert "Attempt: 1" in repair_message
    assert "Error stage: sql_guard" in repair_message
    assert "Error kind: fanout_guard" in repair_message
    assert "Joining fact_order_items can inflate sales amount." in repair_message
    assert "Normalized SQL:" in repair_message
    assert "fact_order_items.item_amount" in repair_message
    assert "Return corrected SQL only." in repair_message
