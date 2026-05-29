from pydantic import BaseModel, Field


class GuardResult(BaseModel):
    allowed: bool
    stage: str
    normalized_sql: str | None = None
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
