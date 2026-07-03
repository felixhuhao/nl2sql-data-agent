import logging

from backend.app.agent.nodes import summarize_node
from backend.app.agent.state import AgentState
from backend.app.execution.runner import QueryResult
from backend.app.i18n import resolve_locale, t


def test_resolve_locale_prefers_explicit_locale_over_accept_language():
    assert resolve_locale("en-US", "zh-CN,zh;q=0.9") == "en"


def test_resolve_locale_uses_accept_language_then_default():
    assert resolve_locale(accept_language="en-US,en;q=0.9") == "en"
    assert resolve_locale(accept_language="fr-FR,fr;q=0.9") == "zh"


def test_i18n_missing_key_falls_back_to_key(caplog):
    caplog.set_level(logging.WARNING)

    assert t("missing.example", "en") == "missing.example"
    assert "Missing i18n key: missing.example" in caplog.text


def test_summarize_node_default_locale_preserves_legacy_summary():
    state = AgentState(
        question="test",
        query_result=QueryResult(columns=["date_value", "sales_amount"], rows=[], row_count=3),
    )

    summarize_node(state)

    assert state.summary == "查询返回 3 行，字段：date_value, sales_amount。"
    assert state.completed_steps == ["summarize"]


def test_summarize_node_uses_english_locale():
    state = AgentState(
        question="test",
        locale="en",
        query_result=QueryResult(columns=["date_value", "sales_amount"], rows=[], row_count=3),
    )

    summarize_node(state)

    assert state.summary == "Query returned 3 rows with columns: date_value, sales_amount."
    assert state.completed_steps == ["summarize"]
