from fastapi import FastAPI

from backend.app.api.metadata import router as metadata_router


app = FastAPI(title="NL2SQL Data Agent")
app.include_router(metadata_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
