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
        user_content = f"{user_content}\n\n{_conversation_followup_rules()}"
    if request.repair is None:
        user_content = f"{user_content}\n\n{_output_format_contract(request)}"

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
                "If the error is fanout_guard, follow the schema-specific SQL generation guidance and aggregate at the correct grain.",
                "Do not repair by reusing a measure that the schema context marks as fanout-prone.",
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
            "Follow any schema-specific SQL generation guidance from the schema context.",
            "If a column is not allowed or missing, replace it only with an allowed column that has the same business role based on schema labels, descriptions, tags, aliases, Metric Definitions, or SQL Generation Guidance.",
            "If no safe same-role replacement exists, remove the invalid projection or filter and preserve the rest of the query rather than inventing a column; keep user-facing dimensions readable and avoid replacing names with *_key columns.",
            _repair_return_instruction(request),
        ]
    )
    return "\n".join(lines)


def _conversation_followup_rules() -> str:
    return "\n".join(
        [
            "Conversation follow-up rules:",
            "First decide whether the new question refines the previous query.",
            "If it is not a follow-up, ignore the previous query and answer standalone.",
            "If it is a follow-up, return a full standalone SQL preserving prior dimensions, filters, metric, and time window unless the user changes them.",
            "For change_kind=dimension, add the requested dimension but keep the previous metric and time window.",
            "For change_kind=filter, add or change only the filter; keep previous dimensions, metric, and time window.",
            "For change_kind=metric, change only the metric; keep previous dimensions, filters, and time window.",
            "For change_kind=time, change only the time window; keep previous dimensions, filters, and metric.",
        ]
    )


def _output_format_contract(request: SQLGenerationRequest) -> str:
    if request.prior_sql:
        return "\n".join(
            [
                "Output format:",
                "OUTPUT_FORMAT=json",
                "Return one JSON object and nothing else.",
                '{ "sql": "SELECT ...", "is_follow_up": true, "change_kind": "dimension" }',
                "sql must be a full standalone SELECT statement.",
                "is_follow_up must be true only when the new question refines the previous query.",
                "change_kind must be one of: dimension, filter, metric, time, none.",
            ]
        )
    return "\n".join(
        [
            "Output format:",
            "OUTPUT_FORMAT=sql",
            "Return one SQL SELECT statement and nothing else.",
            "Do not return JSON.",
        ]
    )


def _repair_return_instruction(request: SQLGenerationRequest) -> str:
    if request.prior_sql:
        return _output_format_contract(request)
    return "\n".join(
        [
            "Output format:",
            "OUTPUT_FORMAT=sql",
            "Return corrected SQL only.",
            "Do not return JSON.",
        ]
    )


def _schema_context_guide() -> list[str]:
    return [
        "Schema context reading guide:",
        "- The Analysis Space section lists the only allowed datasource, operations, tables, and metrics.",
        "- In the Tables section, lines like '- table_name: ...' define available tables; indented lines like '- column_name (TYPE) [tags] - ...' define columns for the most recent table.",
        "- Join Relationships use 'source_table.source_column -> target_table.target_column' to describe allowed join paths and cardinality.",
        "- Metric Definitions use 'label (metric_name) = expression'; use the expression for the calculation and metric_name as the SELECT alias.",
        "- SQL Generation Guidance contains schema-specific rules that override generic examples when both apply.",
        "- Verified Queries are vetted examples; reuse their SQL only when the user's request clearly asks for the same metric, dimensions, filters, and time range.",
        "- If a Verified Query does not clearly match, generate fresh SQL from the schema context and do not carry over filters or time ranges from the example.",
    ]


def _few_shot_examples() -> list[str]:
    return [
        "Few-shot examples:",
        "These examples are illustrative only; do not copy example table, column, or metric names unless they appear in the current schema context.",
        "",
        "Example 1 - OUTPUT_FORMAT=sql with a metric alias:",
        'Question: "Show total value by category"',
        "Schema excerpt: example_events(category, event_value); metric total_value = SUM(example_events.event_value)",
        "Output:",
        "SELECT e.category AS category, SUM(e.event_value) AS total_value",
        "FROM example_events AS e",
        "GROUP BY e.category",
        "ORDER BY total_value DESC",
        "LIMIT 10",
        "",
        "Example 2 - OUTPUT_FORMAT=json for a conversation follow-up:",
        (
            "Previous SQL: SELECT e.category AS category, SUM(e.event_value) AS total_value "
            "FROM example_events AS e GROUP BY e.category"
        ),
        'New question: "Only completed records"',
        "Output:",
        (
            '{"sql": "SELECT e.category AS category, SUM(e.event_value) AS total_value '
            "FROM example_events AS e WHERE e.status = 'completed' GROUP BY e.category "
            'ORDER BY total_value DESC LIMIT 10", "is_follow_up": true, "change_kind": "filter"}'
        ),
        "",
        "Example 3 - repair when OUTPUT_FORMAT=sql:",
        "Failed SQL: SELECT e.category_name FROM example_events AS e",
        "Error: column example_events.category_name is not allowed; column example_events.category is allowed.",
        "Output:",
        "SELECT e.category AS category",
        "FROM example_events AS e",
        "ORDER BY e.category",
        "LIMIT 20",
        "If a repair prompt uses OUTPUT_FORMAT=json, return the corrected standalone SQL in the JSON sql field.",
    ]


def _system_prompt(dialect: str = "duckdb") -> str:
    dialect_lines = DIALECT_INSTRUCTIONS.get(dialect, DIALECT_INSTRUCTIONS["duckdb"])
    return "\n".join(
        [
            "You generate SQL for a governed NL2SQL data agent.",
            "Follow the Output format section exactly. For OUTPUT_FORMAT=sql return only SQL; for OUTPUT_FORMAT=json return only the requested JSON object. Do not include markdown, comments, prose, or explanation.",
            *dialect_lines,
            "Only generate a single SELECT statement.",
            "Use only tables and columns present in the provided schema context.",
            "Use only assets inside the Analysis Space.",
            "Qualify every physical column with its table name or table alias.",
            "Alias every computed projection with a stable snake_case name.",
            "When using a Metric Layer expression, use the metric name from the schema context as the SELECT alias.",
            "For human-readable dimensions, prefer descriptive name/label columns from the schema and do not substitute surrogate *_key columns unless the user asks for IDs or keys.",
            "For ranking questions without an explicit count, return the top 10 rows with ORDER BY and LIMIT 10.",
            "For plain list, sample, or browse-data questions without an explicit count, return representative business columns and LIMIT 20.",
            "For open-ended browse-data questions, prefer identifiers and primary business measures from the schema context; avoid date/time columns unless the user asks for time.",
            "For dimension value lists, ORDER BY the displayed name/label column for deterministic results.",
            "Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, COPY, INSTALL, or LOAD.",
            "Follow schema-specific SQL generation guidance in the schema context when present.",
            "",
            *_schema_context_guide(),
            "",
            *_few_shot_examples(),
        ]
    )
