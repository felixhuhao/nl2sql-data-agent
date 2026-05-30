from pydantic import BaseModel, Field


class MetricCreate(BaseModel):
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    expression: str = Field(min_length=1)
    description: str | None = None
    default_time_column: str | None = None
    allowed_dimensions: list[str] = Field(default_factory=list)


class MetricUpdate(BaseModel):
    label: str | None = None
    expression: str | None = None
    description: str | None = None
    default_time_column: str | None = None
    allowed_dimensions: list[str] | None = None
    enabled: bool | None = None


class MetricResponse(BaseModel):
    name: str
    label: str
    expression: str
    description: str | None
    default_time_column: str | None
    allowed_dimensions: list[str]
    enabled: bool


class AliasCreate(BaseModel):
    table_name: str = Field(min_length=1)
    column_name: str = Field(min_length=1)
    alias: str = Field(min_length=1)


class AliasResponse(BaseModel):
    id: int
    table_name: str
    column_name: str
    alias: str


class VerifiedQueryCreate(BaseModel):
    query_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    verified_by: str = "user"


class VerifiedQueryUpdate(BaseModel):
    question: str | None = None
    sql: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class VerifiedQueryResponse(BaseModel):
    id: str
    question: str
    sql: str
    tags: list[str]
    verified_by: str
    enabled: bool


class AnalysisSpaceUpdate(BaseModel):
    tables: list[str] | None = None
    enabled_metrics: list[str] | None = None
    allowed_operations: list[str] | None = None


class AnalysisSpaceResponse(BaseModel):
    name: str
    datasource: str
    tables: list[str]
    allowed_tables: list[str]
    enabled_metrics: list[str]
    allowed_operations: list[str]


class RelationshipUpdate(BaseModel):
    confidence: float | None = None
    fanout_risk: str | None = None
    source: str | None = None
    description: str | None = None


class RelationshipResponse(BaseModel):
    id: int
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str
    source: str
    confidence: float
    fanout_risk: str
    description: str | None


class MetadataValidationIssue(BaseModel):
    severity: str
    asset_type: str
    asset_id: str
    field: str
    message: str


class MetadataValidationResponse(BaseModel):
    ok: bool
    issues: list[MetadataValidationIssue]
