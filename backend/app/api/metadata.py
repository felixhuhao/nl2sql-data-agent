from fastapi import APIRouter, HTTPException

from backend.app.metadata.service import build_schema_context, list_columns, list_tables
from backend.app.metadata.sync import sync_metadata

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


@router.post("/sync")
def sync_metadata_endpoint() -> dict[str, int]:
    return sync_metadata()


@router.get("/tables")
def list_tables_endpoint() -> list[dict]:
    return list_tables()


@router.get("/tables/{table_name}/columns")
def list_columns_endpoint(table_name: str) -> list[dict]:
    columns = list_columns(table_name)
    if not columns:
        raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")
    return columns


@router.get("/schema-context")
def schema_context_endpoint() -> dict[str, str]:
    return {"schema_context": build_schema_context()}
