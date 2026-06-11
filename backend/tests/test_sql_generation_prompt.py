from backend.app.agent.prompts.sql_generation import build_sql_generation_messages
from backend.app.core.llm_provider import SQLGenerationRequest, SQLRepairContext


def test_sql_generation_prompt_contains_core_constraints():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="查询最近30天每日销售额和订单数",
            schema_context=(
                "# Schema Context\n"
                "\n"
                "## SQL Generation Guidance\n"
                "- For product names, use dim_products.name AS product_name.\n"
                "- For product or category sales amount on item-level joins, use SUM(fact_order_items.item_amount)."
            ),
        )
    )

    system_prompt = messages[0]["content"]
    assert "Follow the Output format section exactly" in system_prompt
    assert "Return SQL only unless" not in system_prompt
    assert "DuckDB SQL dialect" in system_prompt
    assert "single SELECT statement" in system_prompt
    assert "Analysis Space" in system_prompt
    assert "Qualify every physical column" in system_prompt
    assert "Alias every computed projection" in system_prompt
    assert "use the metric name from the schema context as the SELECT alias" in system_prompt
    assert "do not substitute surrogate *_key columns" in system_prompt
    assert "INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, COPY, INSTALL, or LOAD" in system_prompt
    assert "read_csv, read_json, or read_parquet" in system_prompt
    assert "Few-shot examples:" in system_prompt
    assert "illustrative only" in system_prompt
    assert "Example 1 - OUTPUT_FORMAT=sql" in system_prompt
    assert "Example 2 - OUTPUT_FORMAT=json" in system_prompt
    assert "Example 3 - repair" in system_prompt
    assert "dim_products.name AS product_name" not in system_prompt
    assert "fact_order_items.item_amount" not in system_prompt
    assert "fact_orders.payment_amount" not in system_prompt
    assert "sales_amount, order_count, or aov" not in system_prompt
    user_prompt = messages[1]["content"]
    assert "OUTPUT_FORMAT=sql" in user_prompt
    assert "Return one SQL SELECT statement and nothing else." in user_prompt
    assert "Do not return JSON." in user_prompt
    assert "dim_products.name AS product_name" in user_prompt
    assert "SUM(fact_order_items.item_amount)" in user_prompt


def test_sql_generation_prompt_few_shot_examples_are_not_demo_schema_specific():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="List records",
            schema_context="# Schema Context",
        )
    )

    system_prompt = messages[0]["content"]
    assert "example_events" in system_prompt
    assert "do not copy example table, column, or metric names" in system_prompt
    assert '{"sql": "SELECT e.category AS category' in system_prompt
    assert '"is_follow_up": true' in system_prompt
    assert '"change_kind": "filter"' in system_prompt
    assert "Failed SQL: SELECT e.category_name" in system_prompt
    assert "dim_products" not in system_prompt
    assert "fact_orders" not in system_prompt
    assert "fact_order_items" not in system_prompt


def test_sql_generation_prompt_uses_clickhouse_dialect_instructions():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="按月统计销售额",
            schema_context="# Schema Context",
            datasource_name="clickhouse_ecommerce",
            datasource_dialect="clickhouse",
        )
    )

    system_prompt = messages[0]["content"]
    assert "ClickHouse SQL dialect" in system_prompt
    assert "toStartOfMonth()" in system_prompt
    assert "countIf()" in system_prompt
    assert "s3, url, hdfs, remote, or remoteSecure" in system_prompt
    assert "Do not add time filters unless the user asks" in system_prompt


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
    assert "OUTPUT_FORMAT=sql" in messages[1]["content"]


def test_sql_generation_prompt_uses_json_output_format_for_prior_sql():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="换成订单数",
            schema_context="# Schema Context",
            prior_sql="SELECT SUM(payment_amount) AS sales_amount FROM fact_orders",
            prior_summary="Previous query grouped by day.",
        )
    )

    user_prompt = messages[1]["content"]
    assert "Conversation context:" in user_prompt
    assert "Previous query grouped by day." in user_prompt
    assert "Conversation follow-up rules:" in user_prompt
    assert "OUTPUT_FORMAT=json" in user_prompt
    assert "Return one JSON object and nothing else." in user_prompt
    assert '{ "sql": "SELECT ...", "is_follow_up": true, "change_kind": "dimension" }' in user_prompt
    assert "change_kind must be one of: dimension, filter, metric, time, none." in user_prompt
    assert "OUTPUT_FORMAT=sql" not in user_prompt


def test_sql_generation_prompt_includes_olap_hint():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="查询销售额前10的商品同比增长",
            schema_context="# Schema Context",
            olap_intents=["topn", "yoy_mom"],
            olap_hint="TopN / YoY guidance",
        )
    )

    assert "OLAP SQL guidance:" in messages[1]["content"]
    assert "Detected OLAP intents" not in messages[1]["content"]
    assert "TopN / YoY guidance" in messages[1]["content"]


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
    assert "OUTPUT_FORMAT=sql" not in messages[1]["content"]
    assert sum(message["content"].count("Output format:") for message in messages) == 1
    repair_message = messages[3]["content"]
    assert "Attempt: 1" in repair_message
    assert "Error stage: sql_guard" in repair_message
    assert "Error kind: fanout_guard" in repair_message
    assert "Joining fact_order_items can inflate sales amount." in repair_message
    assert "Datasource dialect: duckdb" in repair_message
    assert "Generate SQL valid for the datasource dialect above." in repair_message
    assert "Normalized SQL:" in repair_message
    assert "aggregate at the correct grain" in repair_message
    assert "schema-specific SQL generation guidance" in repair_message
    assert "semantically closest allowed column" in repair_message
    assert "avoid replacing names with *_key columns" in repair_message
    assert "OUTPUT_FORMAT=sql" in repair_message
    assert "Return corrected SQL only." in repair_message
    assert "Do not return JSON." in repair_message


def test_sql_generation_repair_prompt_uses_json_format_for_prior_sql():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="换成订单数",
            schema_context="# Schema Context",
            repair=SQLRepairContext(
                attempt=1,
                original_sql="SELECT SUM(payment_amount) AS sales_amount FROM fact_orders",
                error_stage="sql_guard",
                error_kind="missing_carried_filter",
                error_reason="Missing carried filter channel = 'online'.",
            ),
            prior_sql="SELECT SUM(payment_amount) AS sales_amount FROM fact_orders WHERE channel = 'online'",
            prior_summary="Previous query filtered to online channel.",
        )
    )

    repair_message = messages[3]["content"]
    assert "OUTPUT_FORMAT=json" not in messages[1]["content"]
    assert sum(message["content"].count("Output format:") for message in messages) == 1
    assert "Conversation context:" in repair_message
    assert "Previous query filtered to online channel." in repair_message
    assert "missing filter" in repair_message
    assert "OUTPUT_FORMAT=json" in repair_message
    assert "Return one JSON object and nothing else." in repair_message
    assert "is_follow_up must be true only when the new question refines the previous query." in repair_message
    assert "OUTPUT_FORMAT=sql" not in repair_message


def test_sql_generation_repair_prompt_guides_product_name_repair():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="Show top products by sales in the last 30 days",
            schema_context=(
                "# Schema Context\n"
                "## SQL Generation Guidance\n"
                "- For product names, use dim_products.name AS product_name; do not invent dim_products.product_name.\n"
                "## Tables\n"
                "- dim_products: 商品维表\n"
                "  - name (VARCHAR) - 商品名称"
            ),
            repair=SQLRepairContext(
                attempt=1,
                original_sql="SELECT p.product_name FROM dim_products p",
                error_stage="sql_guard",
                error_kind="scope_guard",
                error_reason="Column dim_products.product_name is not allowed.",
            ),
            olap_intents=["topn"],
            olap_hint="TopN / ranking SQL guidance",
        )
    )

    repair_message = messages[3]["content"]
    assert "dim_products.product_name" in repair_message
    assert "dim_products.name AS product_name" not in repair_message
    assert "dim_products.name AS product_name" in messages[1]["content"]
    assert "schema-specific SQL generation guidance" in repair_message
    assert "avoid replacing names with *_key columns" in repair_message


def test_sql_generation_repair_prompt_preserves_olap_hint():
    messages = build_sql_generation_messages(
        SQLGenerationRequest(
            question="查询销售额前10的商品同比增长",
            schema_context="# Schema Context",
            repair=SQLRepairContext(
                attempt=1,
                original_sql="SELECT product_id FROM fact_orders",
                error_stage="sql_guard",
                error_kind="scope_guard",
                error_reason="Column fact_orders.product_id is not allowed.",
            ),
            olap_intents=["topn", "yoy_mom"],
            olap_hint="TopN / YoY guidance",
        )
    )

    assert "OLAP SQL guidance:" in messages[1]["content"]
    assert "Detected OLAP intents" not in messages[1]["content"]
    assert "TopN / YoY guidance" in messages[1]["content"]
    assert "Detected OLAP intents" not in messages[3]["content"]
    assert "OLAP SQL guidance:" in messages[3]["content"]
    assert "TopN / YoY guidance" in messages[3]["content"]
