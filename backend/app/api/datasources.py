from dataclasses import asdict

from fastapi import APIRouter

from backend.app.connectors.registry import get_datasource_manager

router = APIRouter(prefix="/api", tags=["datasources"])


@router.get("/datasources")
def list_datasources_endpoint() -> dict:
    manager = get_datasource_manager()
    return {
        "sources": [asdict(source) for source in manager.list_sources()],
        "default": manager.default_name,
    }
