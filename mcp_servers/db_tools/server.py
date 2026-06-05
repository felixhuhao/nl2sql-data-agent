from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.app.execution.runner import execute_guarded_sql
from backend.app.metadata.service import (
    list_columns as svc_list_columns,
    list_relationships as svc_list_relationships,
    list_tables as svc_list_tables,
)
from backend.app.sql_guard.guard import guard_sql
from backend.app.sql_guard.scope import build_default_guard_scope
from mcp_servers._common import DatasourceUnavailable, datasource_error, ensure_ready, err, ok, resolve_datasource


def create_server() -> FastMCP:
    server = FastMCP(
        "nl2sql-db-tools",
        instructions="Read-only database metadata and guarded SQL tools for the NL2SQL data agent.",
    )
    server.tool()(list_tables)
    server.tool()(get_table_schema)
    server.tool()(query_readonly)
    return server


def list_tables(datasource: str | None = None) -> dict:
    try:
        datasource_name = resolve_datasource(datasource)
    except DatasourceUnavailable as exc:
        return datasource_error(exc)
    try:
        tables = svc_list_tables(datasource_name=datasource_name)
    except Exception as exc:
        return err("internal", str(exc), {"datasource": datasource_name})
    return ok(
        {
            "datasource": datasource_name,
            "tables": tables,
        }
    )


def get_table_schema(table_name: str, datasource: str | None = None) -> dict:
    try:
        datasource_name = resolve_datasource(datasource)
    except DatasourceUnavailable as exc:
        return datasource_error(exc)

    try:
        tables = svc_list_tables(datasource_name=datasource_name)
    except Exception as exc:
        return err("internal", str(exc), {"datasource": datasource_name, "table": table_name})

    table = next((item for item in tables if item.get("table_name") == table_name), None)
    if table is None:
        return err(
            "not_found",
            f"Table not found: {table_name}",
            {"table": table_name, "datasource": datasource_name},
        )

    try:
        columns = svc_list_columns(table_name, datasource_name=datasource_name)
        relationships = [
            relationship
            for relationship in svc_list_relationships(datasource_name=datasource_name)
            if relationship.get("source_table") == table_name or relationship.get("target_table") == table_name
        ]
    except Exception as exc:
        return err("internal", str(exc), {"datasource": datasource_name, "table": table_name})

    return ok(
        {
            "datasource": datasource_name,
            "table": table,
            "columns": columns,
            "relationships": relationships,
        }
    )


def query_readonly(sql: str, datasource: str | None = None) -> dict:
    try:
        datasource_name = resolve_datasource(datasource)
    except DatasourceUnavailable as exc:
        return datasource_error(exc)

    scope = build_default_guard_scope(datasource_name=datasource_name)
    guard_result = guard_sql(sql, scope=scope, datasource_name=datasource_name)
    if not guard_result.allowed:
        return ok(
            {
                "allowed": False,
                "stage": guard_result.stage,
                "reason": guard_result.reason,
                "warnings": guard_result.warnings,
            }
        )
    if guard_result.normalized_sql is None:
        return err(
            "internal",
            "Guard result does not include normalized_sql.",
            {"datasource": datasource_name},
        )

    try:
        query_result = execute_guarded_sql(guard_result, datasource_name=datasource_name)
    except Exception as exc:
        return err("internal", str(exc), {"datasource": datasource_name})

    return ok(
        {
            "allowed": True,
            "normalized_sql": guard_result.normalized_sql,
            "warnings": guard_result.warnings,
            "columns": query_result.columns,
            "rows": query_result.rows,
            "row_count": query_result.row_count,
            "elapsed_ms": query_result.elapsed_ms,
        }
    )


def main() -> None:
    ensure_ready()
    get_server().run("stdio")


_mcp: FastMCP | None = None


def get_server() -> FastMCP:
    global _mcp
    if _mcp is None:
        _mcp = create_server()
    return _mcp
