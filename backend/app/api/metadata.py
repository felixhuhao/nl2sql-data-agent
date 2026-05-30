from fastapi import APIRouter, HTTPException

from backend.app.metadata.service import (
    MetadataAdminError,
    build_explainability_context,
    build_schema_context,
    create_alias,
    create_metric,
    create_verified_query,
    delete_alias,
    get_analysis_space,
    list_aliases,
    list_columns,
    list_metrics,
    list_relationships,
    list_tables,
    list_verified_queries,
    toggle_metric,
    toggle_verified_query,
    update_analysis_space,
    update_metric,
    update_relationship,
    update_verified_query,
)
from backend.app.metadata.retrieval import retrieve_metadata_assets
from backend.app.metadata.sync import sync_metadata
from backend.app.schemas.metadata_admin import (
    AliasCreate,
    AliasResponse,
    AnalysisSpaceResponse,
    AnalysisSpaceUpdate,
    MetricCreate,
    MetricResponse,
    MetricUpdate,
    RelationshipResponse,
    RelationshipUpdate,
    VerifiedQueryCreate,
    VerifiedQueryResponse,
    VerifiedQueryUpdate,
)

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


@router.put("/analysis-space", response_model=AnalysisSpaceResponse)
def update_analysis_space_endpoint(payload: AnalysisSpaceUpdate) -> dict:
    return _admin_call(update_analysis_space, payload)


@router.get("/verified-queries", response_model=list[VerifiedQueryResponse])
def verified_queries_endpoint(enabled: bool | None = None) -> list[dict]:
    return list_verified_queries(enabled=enabled)


@router.post("/verified-queries", response_model=VerifiedQueryResponse)
def create_verified_query_endpoint(payload: VerifiedQueryCreate) -> dict:
    return _admin_call(create_verified_query, payload)


@router.put("/verified-queries/{query_id}", response_model=VerifiedQueryResponse)
def update_verified_query_endpoint(query_id: str, payload: VerifiedQueryUpdate) -> dict:
    return _admin_call(update_verified_query, query_id, payload)


@router.patch("/verified-queries/{query_id}/toggle", response_model=VerifiedQueryResponse)
def toggle_verified_query_endpoint(query_id: str) -> dict:
    return _admin_call(toggle_verified_query, query_id)


@router.get("/relationships", response_model=list[RelationshipResponse])
def relationships_endpoint() -> list[dict]:
    return list_relationships()


@router.put("/relationships/{relationship_id}", response_model=RelationshipResponse)
def update_relationship_endpoint(relationship_id: int, payload: RelationshipUpdate) -> dict:
    return _admin_call(update_relationship, relationship_id, payload)


@router.get("/metrics", response_model=list[MetricResponse])
def list_metrics_endpoint(enabled: bool | None = None) -> list[dict]:
    return list_metrics(enabled=enabled)


@router.post("/metrics", response_model=MetricResponse)
def create_metric_endpoint(payload: MetricCreate) -> dict:
    return _admin_call(create_metric, payload)


@router.put("/metrics/{name}", response_model=MetricResponse)
def update_metric_endpoint(name: str, payload: MetricUpdate) -> dict:
    return _admin_call(update_metric, name, payload)


@router.patch("/metrics/{name}/toggle", response_model=MetricResponse)
def toggle_metric_endpoint(name: str) -> dict:
    return _admin_call(toggle_metric, name)


@router.get("/aliases", response_model=list[AliasResponse])
def list_aliases_endpoint(table_name: str | None = None) -> list[dict]:
    return list_aliases(table_name=table_name)


@router.post("/aliases", response_model=AliasResponse)
def create_alias_endpoint(payload: AliasCreate) -> dict:
    return _admin_call(create_alias, payload)


@router.delete("/aliases/{alias_id}", status_code=204)
def delete_alias_endpoint(alias_id: int) -> None:
    _admin_call(delete_alias, alias_id)


def _admin_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except MetadataAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
