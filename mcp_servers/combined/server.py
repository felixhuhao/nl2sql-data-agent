from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from backend.app.config import (
    get_settings,
    nl2sql_mcp_allowed_hosts,
    nl2sql_mcp_allowed_origins,
)
from mcp_servers.db_tools.server import get_table_schema, list_tables, query_readonly
from mcp_servers.olap_tools.server import explain_query, metric_catalog_search


def create_server() -> FastMCP:
    server = FastMCP(
        "nl2sql-tools",
        instructions=(
            "Read-only NL2SQL warehouse tools: schema discovery, guarded SQL "
            "execution, EXPLAIN, and metric catalog search."
        ),
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=nl2sql_mcp_allowed_hosts(get_settings()),
            allowed_origins=nl2sql_mcp_allowed_origins(get_settings()),
        ),
    )
    server.tool()(list_tables)
    server.tool()(get_table_schema)
    server.tool()(query_readonly)
    server.tool()(explain_query)
    server.tool()(metric_catalog_search)
    return server


def create_http_app() -> Starlette:
    return get_server().streamable_http_app()


_mcp: FastMCP | None = None


def get_server() -> FastMCP:
    global _mcp
    if _mcp is None:
        _mcp = create_server()
    return _mcp
