from backend.app.core.llm_provider import SQLGenerationRequest


DIALECT_INSTRUCTIONS = {
    "clickhouse": [
        "Use ClickHouse SQL dialect.",
        "Use ClickHouse functions such as toStartOfDay(), toStartOfMonth(), toYYYYMM(), dateDiff(), and toFloat64().",
        "Use countIf() and sumIf() for conditional aggregations when helpful.",
        "Do not use ClickHouse external table functions such as s3, url, hdfs, remote, or remoteSecure.",
        "Do not add time filters unless the user asks for a time range.",
    ],
    "duckdb": [
        "Use DuckDB SQL dialect.",
        "Use DuckDB date functions such as DATE_TRUNC and DATE_DIFF.",
        "Do not use DuckDB external file functions such as read_csv, read_json, or read_parquet.",
    ],
}


def build_sql_generation_messages(request: SQLGenerationRequest) -> list[dict[str, str]]:
    user_content = (
        f"Schema context:\n{request.schema_context}\n\n"
        f"Question:\n{request.question}"
    )
    if request.prior_sql:
        user_content = f"{user_content}\n\nConversation context:\n{request.prior_summary or ''}"
    if request.olap_hint:
        user_content = f"{user_content}\n\nOLAP SQL guidance:\n{request.olap_hint}"
    if request.prior_sql:
        user_content = f"{user_content}\n\n{_conversation_output_contract()}"

    user_message = {
        "role": "user",
        "content": user_content,
    }
    if request.repair is not None:
        return [
            {
                "role": "system",
                "content": _system_prompt(request.datasource_dialect),
            },
            user_message,
            {
                "role": "assistant",
                "content": request.repair.original_sql,
            },
            {
                "role": "user",
                "content": _repair_prompt(request),
            },
        ]

    return [
        {
            "role": "system",
            "content": _system_prompt(request.datasource_dialect),
        },
        user_message,
    ]


def _repair_prompt(request: SQLGenerationRequest) -> str:
    if request.repair is None:
        raise ValueError("repair context is required.")

    lines = [
        "The previous SQL attempt failed.",
        f"Attempt: {request.repair.attempt}",
        f"Error stage: {request.repair.error_stage}",
        f"Error kind: {request.repair.error_kind}",
        f"Error reason: {request.repair.error_reason}",
        f"Datasource dialect: {request.datasource_dialect}",
    ]
    if request.repair.normalized_sql:
        lines.extend(["", "Normalized SQL:", request.repair.normalized_sql])
    if request.olap_hint:
        lines.extend(["", "OLAP SQL guidance:", request.olap_hint])
    if request.prior_sql:
        lines.extend(["", "Conversation context:", request.prior_summary or ""])
    if request.repair.error_kind == "fanout_guard":
        lines.extend(
            [
                "",
                "If the error is fanout_guard, do not aggregate fact_orders.payment_amount after joining fact_order_items.",
                "For product/category sales, use fact_order_items.item_amount.",
            ]
        )
    if request.repair.error_kind == "missing_carried_filter":
        lines.extend(
            [
                "",
                "The SQL is syntactically valid but dropped a carried conversation filter.",
                "Add the missing filter from the error reason to the WHERE clause while preserving the user's requested follow-up change.",
            ]
        )
    lines.extend(
        [
            "",
            "Generate SQL valid for the datasource dialect above.",
            "Fix the SQL using only the provided schema context.",
            "If a column is not allowed or missing, replace it with the semantically closest allowed column from the schema context; keep user-facing dimensions readable and avoid replacing names with *_key columns.",
            "If the failed SQL used dim_products.product_name, use dim_products.name AS product_name instead.",
            _repair_return_instruction(request),
        ]
    )
    return "\n".join(lines)


def _conversation_output_contract() -> str:
    return "\n".join(
        [
            "Conversation follow-up output contract:",
            "First decide whether the new question refines the previous query.",
            "If it is not a follow-up, ignore the previous query and answer standalone.",
            "If it is a follow-up, return a full standalone SQL preserving prior dimensions, filters, metric, and time window unless the user changes them.",
            "For change_kind=dimension, add the requested dimension but keep the previous metric and time window.",
            "For change_kind=filter, add or change only the filter; keep previous dimensions, metric, and time window.",
            "For change_kind=metric, change only the metric; keep previous dimensions, filters, and time window.",
            "For change_kind=time, change only the time window; keep previous dimensions, filters, and metric.",
            "The output MUST be JSON even if the SQL is simple; never return bare SQL in follow-up mode.",
            "Return a single JSON object and nothing else:",
            '{ "sql": "SELECT ...", "is_follow_up": true, "change_kind": "dimension" }',
            "change_kind must be one of: dimension, filter, metric, time, none.",
        ]
    )


def _repair_return_instruction(request: SQLGenerationRequest) -> str:
    if request.prior_sql:
        return (
            "Return a single JSON object and nothing else, preserving the original "
            "is_follow_up/change_kind semantics when applicable."
        )
    return "Return corrected SQL only."


def _system_prompt(dialect: str = "duckdb") -> str:
    dialect_lines = DIALECT_INSTRUCTIONS.get(dialect, DIALECT_INSTRUCTIONS["duckdb"])
    return "\n".join(
        [
            "You generate SQL for a governed NL2SQL data agent.",
            "Return SQL only unless the user prompt explicitly asks for the conversation follow-up JSON object. Do not include markdown, comments, prose, or explanation.",
            *dialect_lines,
            "Only generate a single SELECT statement.",
            "Use only tables and columns present in the provided schema context.",
            "Use only assets inside the Analysis Space.",
            "Qualify every physical column with its table name or table alias.",
            "Alias every computed projection with a stable snake_case name.",
            "When using a Metric Layer expression, use the metric name as the SELECT alias, such as sales_amount, order_count, or aov.",
            "For human-readable dimensions, prefer descriptive name/label columns from the schema and do not substitute surrogate *_key columns unless the user asks for IDs or keys.",
            "For product names, use dim_products.name AS product_name when dim_products is available; do not invent dim_products.product_name and do not use product_key as the product display label.",
            "For ranking questions without an explicit count, return the top 10 rows with ORDER BY and LIMIT 10.",
            "For plain list, sample, or browse-data questions without an explicit count, return representative business columns and LIMIT 20.",
            "For open-ended browse-data questions, prefer order_id and a primary business measure; avoid date/time columns unless the user asks for time.",
            "For dimension value lists, ORDER BY the displayed name/label column for deterministic results.",
            "For user rankings, include dim_users.user_id and dim_users.name AS user_name when dim_users is available.",
            "Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, COPY, INSTALL, or LOAD.",
            "For product or category sales amount, use SUM(fact_order_items.item_amount), not SUM(fact_orders.payment_amount).",
            "Do not aggregate fact_orders.payment_amount after joining fact_order_items; it duplicates order-level amounts.",
            "Prefer verified queries when the user question matches one.",
        ]
    )
