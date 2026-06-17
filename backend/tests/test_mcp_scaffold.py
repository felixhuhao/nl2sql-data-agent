import asyncio
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_servers.combined.server import create_server as create_combined_server
from mcp_servers.db_tools.server import create_server as create_db_server
from mcp_servers.olap_tools.server import create_server as create_olap_server

ROOT = Path(__file__).resolve().parents[2]


def test_db_tools_server_can_list_tools():
    tools = asyncio.run(create_db_server().list_tools())

    assert {tool.name for tool in tools} == {"list_tables", "get_table_schema", "query_readonly"}


def test_olap_tools_server_can_list_tools():
    tools = asyncio.run(create_olap_server().list_tools())

    assert {tool.name for tool in tools} == {"explain_query", "metric_catalog_search"}


def test_combined_http_server_can_list_all_tools():
    tools = asyncio.run(create_combined_server().list_tools())

    assert {tool.name for tool in tools} == {
        "list_tables",
        "get_table_schema",
        "query_readonly",
        "explain_query",
        "metric_catalog_search",
    }


def test_stdio_servers_can_list_tools_with_real_mcp_client():
    async def list_tool_names(module_name: str) -> list[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", module_name],
            cwd=ROOT,
            env=env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return [tool.name for tool in result.tools]

    assert set(asyncio.run(list_tool_names("mcp_servers.db_tools"))) == {
        "list_tables",
        "get_table_schema",
        "query_readonly",
    }
    assert set(asyncio.run(list_tool_names("mcp_servers.olap_tools"))) == {
        "explain_query",
        "metric_catalog_search",
    }
