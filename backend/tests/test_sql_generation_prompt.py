from backend.app.agent.prompts.sql_generation import build_sql_generation_messages
from backend.app.core.llm_provider import SQLGenerationRequest


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
    assert "INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, COPY, INSTALL, or LOAD" in system_prompt
    assert "read_csv, read_json, or read_parquet" in system_prompt


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
