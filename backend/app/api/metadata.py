from fastapi import APIRouter, HTTPException

from backend.app.dataspace.analysis_space import get_default_analysis_space
from backend.app.dataspace.verified_queries import list_verified_queries
from backend.app.metadata.service import build_explainability_context, build_schema_context, list_columns, list_tables
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


@router.get("/analysis-space")
def analysis_space_endpoint() -> dict:
    space = get_default_analysis_space()
    return {
        "name": space.name,
        "datasource": space.datasource,
        "tables": list(space.tables),
        "enabled_metrics": list(space.enabled_metrics),
        "allowed_operations": list(space.allowed_operations),
    }


@router.get("/verified-queries")
def verified_queries_endpoint() -> list[dict]:
    return [
        {
            "id": query.id,
            "question": query.question,
            "sql": query.sql,
            "tags": list(query.tags),
            "verified_by": query.verified_by,
        }
        for query in list_verified_queries()
    ]
