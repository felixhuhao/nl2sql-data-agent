from fastapi import APIRouter, HTTPException

from backend.app.metadata.service import (
    build_explainability_context,
    build_schema_context,
    get_analysis_space,
    list_columns,
    list_tables,
    list_verified_queries,
)
from backend.app.metadata.retrieval import retrieve_metadata_assets
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


@router.get("/explainability-context")
def explainability_context_endpoint() -> dict:
    return build_explainability_context()


@router.get("/retrieve")
def retrieve_metadata_endpoint(question: str) -> dict:
    if not question.strip():
        raise HTTPException(status_code=400, detail="question is required")
    return retrieve_metadata_assets(question)


@router.get("/analysis-space")
def analysis_space_endpoint() -> dict:
    return get_analysis_space()


@router.get("/verified-queries")
def verified_queries_endpoint() -> list[dict]:
    return list_verified_queries()
