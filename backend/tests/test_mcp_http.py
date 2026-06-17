from fastapi.routing import Mount

from backend.app import main


def test_fastapi_app_mounts_streamable_mcp_endpoint():
    mounts = [route for route in main.app.routes if isinstance(route, Mount)]

    assert any(route.path == "/mcp" for route in mounts)

