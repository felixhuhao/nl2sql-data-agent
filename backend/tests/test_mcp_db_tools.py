from backend.app.execution.runner import QueryResult
from backend.app.sql_guard.models import GuardResult
from backend.app.sql_guard.scope import GuardScope
from mcp_servers.db_tools import server


def test_list_tables_returns_structured_envelope(monkeypatch):
    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(
        server,
        "svc_list_tables",
        lambda datasource_name: [{"table_name": "fact_orders", "row_count": 10}],
    )

    result = server.list_tables()

    assert result == {
        "ok": True,
        "data": {
            "datasource": "duckdb_ecommerce",
            "tables": [{"table_name": "fact_orders", "row_count": 10}],
        },
    }


def test_list_tables_returns_datasource_error(monkeypatch):
    def reject(datasource=None):
        raise server.DatasourceUnavailable("missing", "Unknown datasource: missing")

    monkeypatch.setattr(server, "resolve_datasource", reject)

    result = server.list_tables("missing")

    assert result == {
        "ok": False,
        "error": {
            "kind": "datasource_unavailable",
            "message": "Unknown datasource: missing",
            "detail": {"datasource": "missing"},
        },
    }


def test_list_tables_wraps_service_errors(monkeypatch):
    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(
        server,
        "svc_list_tables",
        lambda datasource_name: (_ for _ in ()).throw(RuntimeError("sqlite unavailable")),
    )

    result = server.list_tables()

    assert result == {
        "ok": False,
        "error": {
            "kind": "internal",
            "message": "sqlite unavailable",
            "detail": {"datasource": "duckdb_ecommerce"},
        },
    }


def test_get_table_schema_returns_columns_and_related_relationships(monkeypatch):
    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(
        server,
        "svc_list_tables",
        lambda datasource_name: [
            {"table_name": "fact_orders", "description": "orders"},
            {"table_name": "dim_date", "description": "date"},
        ],
    )
    monkeypatch.setattr(
        server,
        "svc_list_columns",
        lambda table_name, datasource_name: [{"column_name": "date_key", "data_type": "INTEGER"}],
    )
    monkeypatch.setattr(
        server,
        "svc_list_relationships",
        lambda datasource_name: [
            {
                "source_table": "fact_orders",
                "source_column": "date_key",
                "target_table": "dim_date",
                "target_column": "date_key",
            },
            {
                "source_table": "dim_channels",
                "source_column": "channel_key",
                "target_table": "fact_orders",
                "target_column": "channel_key",
            },
            {
                "source_table": "dim_regions",
                "source_column": "region_key",
                "target_table": "dim_users",
                "target_column": "region_key",
            },
        ],
    )

    result = server.get_table_schema("fact_orders")

    assert result["ok"] is True
    assert result["data"]["datasource"] == "duckdb_ecommerce"
    assert result["data"]["table"] == {"table_name": "fact_orders", "description": "orders"}
    assert result["data"]["columns"] == [{"column_name": "date_key", "data_type": "INTEGER"}]
    assert result["data"]["relationships"] == [
        {
            "source_table": "fact_orders",
            "source_column": "date_key",
            "target_table": "dim_date",
            "target_column": "date_key",
        },
        {
            "source_table": "dim_channels",
            "source_column": "channel_key",
            "target_table": "fact_orders",
            "target_column": "channel_key",
        },
    ]


def test_get_table_schema_returns_datasource_error(monkeypatch):
    def reject(datasource=None):
        raise server.DatasourceUnavailable("missing", "Unknown datasource: missing")

    monkeypatch.setattr(server, "resolve_datasource", reject)

    result = server.get_table_schema("fact_orders", datasource="missing")

    assert result == {
        "ok": False,
        "error": {
            "kind": "datasource_unavailable",
            "message": "Unknown datasource: missing",
            "detail": {"datasource": "missing"},
        },
    }


def test_get_table_schema_returns_not_found(monkeypatch):
    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "svc_list_tables", lambda datasource_name: [])

    result = server.get_table_schema("missing")

    assert result == {
        "ok": False,
        "error": {
            "kind": "not_found",
            "message": "Table not found: missing",
            "detail": {"table": "missing", "datasource": "duckdb_ecommerce"},
        },
    }


def test_get_table_schema_wraps_service_errors(monkeypatch):
    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(
        server,
        "svc_list_tables",
        lambda datasource_name: (_ for _ in ()).throw(RuntimeError("sqlite unavailable")),
    )

    result = server.get_table_schema("fact_orders")

    assert result == {
        "ok": False,
        "error": {
            "kind": "internal",
            "message": "sqlite unavailable",
            "detail": {"datasource": "duckdb_ecommerce", "table": "fact_orders"},
        },
    }


def test_get_table_schema_wraps_column_or_relationship_errors(monkeypatch):
    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "svc_list_tables", lambda datasource_name: [{"table_name": "fact_orders"}])
    monkeypatch.setattr(
        server,
        "svc_list_columns",
        lambda table_name, datasource_name: (_ for _ in ()).throw(RuntimeError("column load failed")),
    )

    result = server.get_table_schema("fact_orders")

    assert result == {
        "ok": False,
        "error": {
            "kind": "internal",
            "message": "column load failed",
            "detail": {"datasource": "duckdb_ecommerce", "table": "fact_orders"},
        },
    }


def test_query_readonly_rejects_delete_without_executing(monkeypatch):
    executed = []
    scope = GuardScope(allowed_tables=frozenset({"fact_orders"}), table_columns={"fact_orders": frozenset({"order_id"})})

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)

    def fake_execute(guard_result: GuardResult, datasource_name: str):
        executed.append((guard_result, datasource_name))
        raise AssertionError("Rejected SQL must not execute.")

    monkeypatch.setattr(server, "execute_guarded_sql", fake_execute)

    result = server.query_readonly("DELETE FROM fact_orders")

    assert result["ok"] is True
    assert result["data"]["allowed"] is False
    assert result["data"]["stage"] == "operation_guard"
    assert "DELETE is not allowed" in result["data"]["reason"]
    assert executed == []


def test_query_readonly_rejects_out_of_scope_table_without_executing(monkeypatch):
    executed = []
    scope = GuardScope(allowed_tables=frozenset({"fact_orders"}), table_columns={"fact_orders": frozenset({"order_id"})})

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(server, "execute_guarded_sql", lambda guard_result, datasource_name: executed.append(guard_result))

    result = server.query_readonly("SELECT user_id FROM dim_users")

    assert result["ok"] is True
    assert result["data"]["allowed"] is False
    assert result["data"]["stage"] == "scope_guard"
    assert executed == []


def test_query_readonly_does_not_catch_guard_failures(monkeypatch):
    scope = GuardScope(allowed_tables=frozenset({"fact_orders"}), table_columns={"fact_orders": frozenset({"order_id"})})

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(server, "guard_sql", lambda sql, scope, datasource_name: (_ for _ in ()).throw(RuntimeError("guard bug")))

    try:
        server.query_readonly("SELECT order_id FROM fact_orders")
    except RuntimeError as exc:
        assert str(exc) == "guard bug"
    else:
        raise AssertionError("Guard failures must not be wrapped as tool errors.")


def test_query_readonly_returns_internal_when_guard_has_no_normalized_sql(monkeypatch):
    scope = GuardScope(allowed_tables=frozenset({"fact_orders"}), table_columns={"fact_orders": frozenset({"order_id"})})

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(
        server,
        "guard_sql",
        lambda sql, scope, datasource_name: GuardResult(allowed=True, stage="syntax_guard", normalized_sql=None),
    )

    result = server.query_readonly("SELECT order_id FROM fact_orders")

    assert result == {
        "ok": False,
        "error": {
            "kind": "internal",
            "message": "Guard result does not include normalized_sql.",
            "detail": {"datasource": "duckdb_ecommerce"},
        },
    }


def test_query_readonly_executes_guarded_select(monkeypatch):
    captured = {}
    scope = GuardScope(
        allowed_tables=frozenset({"fact_orders"}),
        table_columns={"fact_orders": frozenset({"order_id", "payment_amount"})},
    )

    def fake_execute(guard_result: GuardResult, datasource_name: str):
        captured["guard_result"] = guard_result
        captured["datasource"] = datasource_name
        return QueryResult(columns=["order_id"], rows=[[1]], row_count=1, elapsed_ms=1.23)

    monkeypatch.setattr(server, "resolve_datasource", lambda datasource=None: "duckdb_ecommerce")
    monkeypatch.setattr(server, "build_default_guard_scope", lambda datasource_name: scope)
    monkeypatch.setattr(server, "execute_guarded_sql", fake_execute)

    result = server.query_readonly("SELECT order_id FROM fact_orders")

    assert result == {
        "ok": True,
        "data": {
            "allowed": True,
            "normalized_sql": "SELECT order_id FROM fact_orders LIMIT 500",
            "warnings": ["LIMIT 500 was added automatically."],
            "columns": ["order_id"],
            "rows": [[1]],
            "row_count": 1,
            "elapsed_ms": 1.23,
        },
    }
    assert captured["datasource"] == "duckdb_ecommerce"
    assert captured["guard_result"].allowed is True
