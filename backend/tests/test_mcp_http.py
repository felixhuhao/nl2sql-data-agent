from __future__ import annotations

import asyncio
import json

import httpx
from fastapi.routing import Mount
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.responses import JSONResponse

from backend.app import main
from backend.app.config import get_settings


EXPECTED_TOOLS = [
    "explain_query",
    "get_table_schema",
    "list_tables",
    "metric_catalog_search",
    "query_readonly",
]


def test_fastapi_app_mounts_streamable_mcp_endpoint():
    mounts = [route for route in main.app.routes if isinstance(route, Mount)]

    assert any(route.path == "/mcp" for route in mounts)


def test_streamable_http_mcp_rejects_missing_or_wrong_token(monkeypatch):
    monkeypatch.setenv("NL2SQL_MCP_SERVICE_TOKEN", "secret")
    get_settings.cache_clear()

    try:
        missing = asyncio.run(_middleware_status([]))
        wrong = asyncio.run(_middleware_status([(b"x-service-token", b"wrong")]))
        malformed = asyncio.run(_middleware_status([(b"x-service-token", b"\xff")]))
    finally:
        get_settings.cache_clear()

    assert missing == 401
    assert wrong == 401
    assert malformed == 401


def test_streamable_http_mcp_is_open_when_token_unset(monkeypatch):
    monkeypatch.delenv("NL2SQL_MCP_SERVICE_TOKEN", raising=False)
    get_settings.cache_clear()

    try:
        status = asyncio.run(_middleware_status([]))
    finally:
        get_settings.cache_clear()

    assert status == 200


def test_streamable_http_mcp_lists_tools_from_docker_host_and_guards_query(monkeypatch):
    monkeypatch.setenv("NL2SQL_MCP_SERVICE_TOKEN", "secret")
    get_settings.cache_clear()

    try:
        tools, guard = asyncio.run(
            _list_tools_and_guard_query(
                base_url="http://backend:8000",
                headers={"X-Service-Token": "secret"},
            )
        )
    finally:
        get_settings.cache_clear()

    assert tools == EXPECTED_TOOLS
    assert guard["ok"] is True
    assert guard["data"]["allowed"] is False
    assert guard["data"]["stage"] == "operation_guard"


async def _list_tools_and_guard_query(
    *, headers: dict[str, str], base_url: str = "http://127.0.0.1:8000"
) -> tuple[list[str], dict]:
    async with main.lifespan(main.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url=base_url,
            headers=headers,
        ) as http_client:
            async with streamable_http_client(
                f"{base_url}/mcp/",
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = sorted(tool.name for tool in (await session.list_tools()).tools)
                    result = await session.call_tool(
                        "query_readonly", {"sql": "DELETE FROM fact_orders"}
                    )
                    payload = json.loads(result.content[0].text)
                    return tools, payload


async def _middleware_status(headers: list[tuple[bytes, bytes]]) -> int:
    async def ok_app(scope, receive, send):
        response = JSONResponse({"ok": True})
        await response(scope, receive, send)

    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict):
        messages.append(message)

    middleware = main.ServiceTokenMiddleware(ok_app)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp/",
            "headers": headers,
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    return start["status"]
