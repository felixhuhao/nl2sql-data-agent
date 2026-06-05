from __future__ import annotations

import sqlglot
from mcp.server.fastmcp import FastMCP
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.agent.performance import parse_plan_hints
from backend.app.connectors.registry import get_datasource_manager
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.sql_guard.guard import guard_sql
from backend.app.sql_guard.scope import build_default_guard_scope
from mcp_servers._common import DatasourceUnavailable, datasource_error, ensure_ready, err, ok, resolve_datasource


def create_server() -> FastMCP:
    server = FastMCP(
        "nl2sql-olap-tools",
        instructions="Read-only OLAP explain and metric catalog tools for the NL2SQL data agent.",
    )
    server.tool()(explain_query)
    server.tool()(metric_catalog_search)
    return server


def explain_query(sql: str, datasource: str | None = None) -> dict:
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
        connector = get_datasource_manager().get(datasource_name)
        explain_result = connector.explain(guard_result.normalized_sql)
        matched_tables = _matched_tables_from_sql(
            guard_result.normalized_sql,
            dialect=getattr(connector, "dialect", "duckdb"),
        )
        plan_hints = (
            parse_plan_hints(explain_result, matched_tables=matched_tables, sql=guard_result.normalized_sql)
            if explain_result
            else []
        )
    except Exception as exc:
        return err("internal", str(exc), {"datasource": datasource_name})

    return ok(
        {
            "allowed": True,
            "normalized_sql": guard_result.normalized_sql,
            "warnings": guard_result.warnings,
            "explain": explain_result,
            "plan_hints": plan_hints,
        }
    )


def metric_catalog_search(query: str, datasource: str | None = None) -> dict:
    try:
        datasource_name = resolve_datasource(datasource)
    except DatasourceUnavailable as exc:
        return datasource_error(exc)

    try:
        assets = retrieve_metadata_assets(query, datasource_name=datasource_name)
    except Exception as exc:
        return err("internal", str(exc), {"datasource": datasource_name})

    return ok(
        {
            "datasource": datasource_name,
            "query": query,
            "metrics": assets.get("metrics", []),
            "tables": assets.get("tables", []),
            "verified_queries": assets.get("verified_queries", []),
            "fallback_used": assets.get("fallback_used", False),
        }
    )


def main() -> None:
    ensure_ready()
    get_server().run("stdio")


def _matched_tables_from_sql(sql: str, dialect: str) -> list[str]:
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return []
    return sorted({table.name for table in expression.find_all(exp.Table) if table.name})


_mcp: FastMCP | None = None


def get_server() -> FastMCP:
    global _mcp
    if _mcp is None:
        _mcp = create_server()
    return _mcp
