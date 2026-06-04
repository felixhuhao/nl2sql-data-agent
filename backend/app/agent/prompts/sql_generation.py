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
    user_message = {
        "role": "user",
        "content": (
            f"Schema context:\n{request.schema_context}\n\n"
            f"Question:\n{request.question}"
        ),
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
    if request.repair.error_kind == "fanout_guard":
        lines.extend(
            [
                "",
                "If the error is fanout_guard, do not aggregate fact_orders.payment_amount after joining fact_order_items.",
                "For product/category sales, use fact_order_items.item_amount.",
            ]
        )
    lines.extend(
        [
            "",
            "Generate SQL valid for the datasource dialect above.",
            "Fix the SQL using only the provided schema context.",
            "Return corrected SQL only.",
        ]
    )
    return "\n".join(lines)


def _system_prompt(dialect: str = "duckdb") -> str:
    dialect_lines = DIALECT_INSTRUCTIONS.get(dialect, DIALECT_INSTRUCTIONS["duckdb"])
    return "\n".join(
        [
            "You generate SQL for a governed NL2SQL data agent.",
            "Return SQL only. Do not include markdown, comments, prose, or explanation.",
            *dialect_lines,
            "Only generate a single SELECT statement.",
            "Use only tables and columns present in the provided schema context.",
            "Use only assets inside the Analysis Space.",
            "Qualify every physical column with its table name or table alias.",
            "Alias every computed projection with a stable snake_case name.",
            "When using a Metric Layer expression, use the metric name as the SELECT alias, such as sales_amount, order_count, or aov.",
            "Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, COPY, INSTALL, or LOAD.",
            "For product or category sales amount, use SUM(fact_order_items.item_amount), not SUM(fact_orders.payment_amount).",
            "Do not aggregate fact_orders.payment_amount after joining fact_order_items; it duplicates order-level amounts.",
            "Prefer verified queries when the user question matches one.",
        ]
    )
