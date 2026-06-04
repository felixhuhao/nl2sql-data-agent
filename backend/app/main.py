from fastapi import FastAPI

from backend.app.api.chat import router as chat_router
from backend.app.api.datasources import router as datasources_router
from backend.app.api.metadata import router as metadata_router
from backend.app.config import get_settings
from backend.app.connectors.registry import get_datasource_manager


app = FastAPI(title="NL2SQL Data Agent")
app.state.datasource_manager = get_datasource_manager()
app.include_router(datasources_router)
app.include_router(chat_router)
app.include_router(metadata_router)


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "llm_provider": get_settings().llm_provider}
