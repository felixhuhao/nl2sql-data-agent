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
