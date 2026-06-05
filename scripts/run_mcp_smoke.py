from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def main() -> None:
    async with _mcp_session("mcp_servers.db_tools") as db:
        db_tools = [tool.name for tool in (await db.list_tools()).tools]
        _assert_tools(db_tools, ["list_tables", "get_table_schema", "query_readonly"])

        tables_result = await _call(db, "list_tables", {})
        _assert_ok(tables_result)
        tables = tables_result["data"]["tables"]
        assert tables, "list_tables should return at least one table"
        table_name = "fact_orders" if any(table.get("table_name") == "fact_orders" for table in tables) else tables[0]["table_name"]

        schema_result = await _call(db, "get_table_schema", {"table_name": table_name})
        _assert_ok(schema_result)
        assert schema_result["data"]["columns"], "get_table_schema should return columns"

        guard_result = await _call(
            db,
            "query_readonly",
            {"sql": "DELETE FROM fact_orders WHERE order_id = 'O00000001'"},
        )
        _assert_ok(guard_result)
        assert guard_result["data"]["allowed"] is False
        assert guard_result["data"]["stage"] == "operation_guard"

    async with _mcp_session("mcp_servers.olap_tools") as olap:
        olap_tools = [tool.name for tool in (await olap.list_tools()).tools]
        _assert_tools(olap_tools, ["explain_query", "metric_catalog_search"])

        explain_result = await _call(olap, "explain_query", {"sql": "SELECT order_id FROM fact_orders"})
        _assert_ok(explain_result)
        assert explain_result["data"]["allowed"] is True
        assert explain_result["data"]["explain"], "explain_query should return a plan"

        search_result = await _call(olap, "metric_catalog_search", {"query": "销售额"})
        _assert_ok(search_result)
        assert "metrics" in search_result["data"]

    print("MCP smoke passed: db_tools + olap_tools + guarded DELETE rejection")


def _mcp_session(module_name: str):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module_name],
        cwd=ROOT,
        env=env,
    )
    return _session_context(params)


class _session_context:
    def __init__(self, params: StdioServerParameters) -> None:
        self._params = params
        self._stdio_context = None
        self._session_context = None

    async def __aenter__(self) -> ClientSession:
        self._stdio_context = stdio_client(self._params)
        read_stream, write_stream = await self._stdio_context.__aenter__()
        self._session_context = ClientSession(read_stream, write_stream)
        session = await self._session_context.__aenter__()
        await session.initialize()
        return session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._session_context is not None:
                await self._session_context.__aexit__(exc_type, exc, tb)
        finally:
            if self._stdio_context is not None:
                await self._stdio_context.__aexit__(exc_type, exc, tb)


async def _call(session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> dict:
    result = await session.call_tool(tool_name, arguments)
    assert not result.isError, f"{tool_name} returned MCP error: {result}"
    assert result.content, f"{tool_name} returned no content"
    payload = result.content[0]
    return json.loads(payload.text)


def _assert_tools(actual: list[str], expected: list[str]) -> None:
    assert sorted(actual) == sorted(expected), f"Expected tools {expected}, got {actual}"


def _assert_ok(result: dict) -> None:
    assert result.get("ok") is True, result


if __name__ == "__main__":
    asyncio.run(main())
