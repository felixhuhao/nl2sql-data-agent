from backend.app.core.llm_provider import SQLGenerationRequest


def build_sql_generation_messages(request: SQLGenerationRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _system_prompt(),
        },
        {
            "role": "user",
            "content": (
                f"Schema context:\n{request.schema_context}\n\n"
                f"Question:\n{request.question}"
            ),
        },
    ]


def _system_prompt() -> str:
    return "\n".join(
        [
            "You generate SQL for a governed NL2SQL data agent.",
            "Return SQL only. Do not include markdown, comments, prose, or explanation.",
            "Use DuckDB SQL dialect.",
            "Only generate a single SELECT statement.",
            "Use only tables and columns present in the provided schema context.",
            "Use only assets inside the Analysis Space.",
            "Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, COPY, INSTALL, or LOAD.",
            "Do not use DuckDB external file functions such as read_csv, read_json, or read_parquet.",
            "Prefer verified queries when the user question matches one.",
        ]
    )
