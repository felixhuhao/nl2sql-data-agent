from backend.app.core.llm_provider import SQLGenerationRequest


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
                "content": _system_prompt(),
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
            "content": _system_prompt(),
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
            "Fix the SQL using only the provided schema context.",
            "Return corrected SQL only.",
        ]
    )
    return "\n".join(lines)


def _system_prompt() -> str:
    return "\n".join(
        [
            "You generate SQL for a governed NL2SQL data agent.",
            "Return SQL only. Do not include markdown, comments, prose, or explanation.",
            "Use DuckDB SQL dialect.",
            "Only generate a single SELECT statement.",
            "Use only tables and columns present in the provided schema context.",
            "Use only assets inside the Analysis Space.",
            "Qualify every physical column with its table name or table alias.",
            "Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, COPY, INSTALL, or LOAD.",
            "Do not use DuckDB external file functions such as read_csv, read_json, or read_parquet.",
            "For product or category sales amount, use SUM(fact_order_items.item_amount), not SUM(fact_orders.payment_amount).",
            "Do not aggregate fact_orders.payment_amount after joining fact_order_items; it duplicates order-level amounts.",
            "Prefer verified queries when the user question matches one.",
        ]
    )
