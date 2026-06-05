from backend.app.sql_guard.scope import GuardScope
from mcp_servers.olap_tools import server


def test_explain_query_rejects_dangerous_sql_without_explain(monkeypatch):
    class FakeManager:
        def get(self, datasource_name: str):
            raise AssertionError("Rejected SQL must not reach datasource connector.")

    scope = GuardScope(allowed_tables=frozenset({"fact_orders"}), table_columns={"fact_orders": frozenset({"order_id"})})

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(server, "get_datasource_manager", lambda: FakeManager())

    result = server.explain_query("DROP TABLE fact_orders")

    assert result["ok"] is True
    assert result["data"]["allowed"] is False
    assert result["data"]["stage"] == "operation_guard"
    assert "DROP is not allowed" in result["data"]["reason"]


def test_explain_query_returns_plan_and_hints(monkeypatch):
    class FakeConnector:
        def explain(self, sql: str):
            assert sql == "SELECT order_id FROM fact_orders LIMIT 500"
            return {"dialect": "duckdb", "format": "text", "lines": ["SEQ_SCAN fact_orders"]}

    class FakeManager:
        def get(self, datasource_name: str):
            assert datasource_name == "duckdb_ecommerce"
            return FakeConnector()

    scope = GuardScope(
        allowed_tables=frozenset({"fact_orders"}),
        table_columns={"fact_orders": frozenset({"order_id"})},
    )

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(server, "get_datasource_manager", lambda: FakeManager())

    result = server.explain_query("SELECT order_id FROM fact_orders")

    assert result == {
        "ok": True,
        "data": {
            "allowed": True,
            "normalized_sql": "SELECT order_id FROM fact_orders LIMIT 500",
            "warnings": ["LIMIT 500 was added automatically."],
            "explain": {"dialect": "duckdb", "format": "text", "lines": ["SEQ_SCAN fact_orders"]},
            "plan_hints": ["建议添加明确的时间范围过滤以减少 ClickHouse 扫描。"],
        },
    }


def test_explain_query_passes_matched_tables_to_plan_hints(monkeypatch):
    captured = {}

    class FakeConnector:
        dialect = "duckdb"

        def explain(self, sql: str):
            return {"dialect": "duckdb", "format": "text", "lines": ["SEQ_SCAN fact_orders"]}

    class FakeManager:
        def get(self, datasource_name: str):
            return FakeConnector()

    scope = GuardScope(
        allowed_tables=frozenset({"fact_orders"}),
        table_columns={"fact_orders": frozenset({"order_id"})},
    )

    def fake_parse_plan_hints(explain_result, matched_tables=None, sql=""):
        captured["matched_tables"] = matched_tables
        captured["sql"] = sql
        return ["hint"]

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(server, "get_datasource_manager", lambda: FakeManager())
    monkeypatch.setattr(server, "parse_plan_hints", fake_parse_plan_hints)

    result = server.explain_query("SELECT order_id FROM fact_orders")

    assert result["ok"] is True
    assert result["data"]["plan_hints"] == ["hint"]
    assert captured == {
        "matched_tables": ["fact_orders"],
        "sql": "SELECT order_id FROM fact_orders LIMIT 500",
    }


def test_explain_query_returns_datasource_error(monkeypatch):
    def reject(datasource=None):
        raise server.DatasourceUnavailable("clickhouse_ecommerce", "Unknown datasource: clickhouse_ecommerce")

    monkeypatch.setattr(server, "resolve_datasource", reject)

    result = server.explain_query("SELECT 1", datasource="clickhouse_ecommerce")

    assert result == {
        "ok": False,
        "error": {
            "kind": "datasource_unavailable",
            "message": "Unknown datasource: clickhouse_ecommerce",
            "detail": {"datasource": "clickhouse_ecommerce"},
        },
    }


def test_explain_query_does_not_catch_guard_failures(monkeypatch):
    scope = GuardScope(allowed_tables=frozenset({"fact_orders"}), table_columns={"fact_orders": frozenset({"order_id"})})

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(server, "guard_sql", lambda sql, scope, datasource_name: (_ for _ in ()).throw(RuntimeError("guard bug")))

    try:
        server.explain_query("SELECT order_id FROM fact_orders")
    except RuntimeError as exc:
        assert str(exc) == "guard bug"
    else:
        raise AssertionError("Guard failures must not be wrapped as tool errors.")


def test_explain_query_returns_internal_when_guard_has_no_normalized_sql(monkeypatch):
    scope = GuardScope(allowed_tables=frozenset({"fact_orders"}), table_columns={"fact_orders": frozenset({"order_id"})})

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(
        server,
        "guard_sql",
        lambda sql, scope, datasource_name: type(
            "Guard",
            (),
            {
                "allowed": True,
                "normalized_sql": None,
                "stage": "syntax_guard",
                "reason": None,
                "warnings": [],
            },
        )(),
    )

    result = server.explain_query("SELECT order_id FROM fact_orders")

    assert result == {
        "ok": False,
        "error": {
            "kind": "internal",
            "message": "Guard result does not include normalized_sql.",
            "detail": {"datasource": "duckdb_ecommerce"},
        },
    }


def test_metric_catalog_search_returns_retrieval_assets(monkeypatch):
    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(
        server,
        "retrieve_metadata_assets",
        lambda query, datasource_name: {
            "metrics": [{"name": "sales_amount", "score": 10}],
            "tables": [{"table_name": "fact_orders", "score": 3}],
            "verified_queries": [{"id": "recent_30d_daily_sales"}],
            "fallback_used": False,
        },
    )

    result = server.metric_catalog_search("销售额")

    assert result == {
        "ok": True,
        "data": {
            "datasource": "duckdb_ecommerce",
            "query": "销售额",
            "metrics": [{"name": "sales_amount", "score": 10}],
            "tables": [{"table_name": "fact_orders", "score": 3}],
            "verified_queries": [{"id": "recent_30d_daily_sales"}],
            "fallback_used": False,
        },
    }


def test_metric_catalog_search_returns_datasource_error(monkeypatch):
    def reject(datasource=None):
        raise server.DatasourceUnavailable("missing", "Unknown datasource: missing")

    monkeypatch.setattr(server, "resolve_datasource", reject)

    result = server.metric_catalog_search("销售额", datasource="missing")

    assert result == {
        "ok": False,
        "error": {
            "kind": "datasource_unavailable",
            "message": "Unknown datasource: missing",
            "detail": {"datasource": "missing"},
        },
    }


def test_metric_catalog_search_returns_internal_on_retrieval_error(monkeypatch):
    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(
        server,
        "retrieve_metadata_assets",
        lambda query, datasource_name: (_ for _ in ()).throw(RuntimeError("retrieval failed")),
    )

    result = server.metric_catalog_search("销售额")

    assert result == {
        "ok": False,
        "error": {
            "kind": "internal",
            "message": "retrieval failed",
            "detail": {"datasource": "duckdb_ecommerce"},
        },
    }
