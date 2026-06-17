from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.chat import router as chat_router
from backend.app.api.datasources import router as datasources_router
from backend.app.api.metadata import router as metadata_router
from backend.app.config import deepseek_config_available, effective_llm_provider_name, get_settings, semantic_guard_mode
from backend.app.connectors.registry import get_datasource_manager


try:
    from mcp_servers.combined.server import (
        create_http_app as create_mcp_http_app,
        get_server as get_mcp_server,
    )
except ModuleNotFoundError as exc:
    if exc.name not in {"mcp", "mcp_servers"}:
        raise
    create_mcp_http_app = None
    get_mcp_server = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_mcp_server is None:
        yield
        return
    async with get_mcp_server().session_manager.run():
        yield


app = FastAPI(title="NL2SQL Data Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.datasource_manager = get_datasource_manager()
app.include_router(datasources_router)
app.include_router(chat_router)
app.include_router(metadata_router)

if create_mcp_http_app is not None:
    app.mount("/mcp", create_mcp_http_app())


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    settings = get_settings()
    guard_mode = semantic_guard_mode(settings)
    verifier_available = deepseek_config_available(settings)
    verifier_status = "disabled" if guard_mode == "off" else ("available" if verifier_available else "unavailable")
    status = "degraded" if guard_mode == "enforce" and not verifier_available else "ok"
    return {
        "status": status,
        "llm_provider": effective_llm_provider_name(),
        "semantic_guard": guard_mode,
        "semantic_verifier": verifier_status,
    }
