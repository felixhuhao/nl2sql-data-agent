from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_env: str = "local"
    duckdb_path: Path = Field(default=PROJECT_ROOT / "data" / "ecommerce.duckdb")
    sqlite_path: Path = Field(default=PROJECT_ROOT / "data" / "metadata.sqlite")
    dataset_current_date: str = "2025-12-31"
    llm_provider: str = "mock"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    vector_enabled: bool = False
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_prefix: str = "nl2sql"
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    vector_similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    value_vector_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    default_datasource: str = "duckdb_ecommerce"
    clickhouse_enabled: bool = False
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "default"
    clickhouse_password: str = "clickhouse"
    clickhouse_database: str = "ecommerce"
    clickhouse_readonly: bool = True
    clickhouse_max_execution_time: int = 30
    clickhouse_max_result_rows: int = 10000

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / "backend" / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def resolved_duckdb_path(self) -> Path:
        return self._resolve_path(self.duckdb_path)

    def resolved_sqlite_path(self) -> Path:
        return self._resolve_path(self.sqlite_path)

    @staticmethod
    def _resolve_path(path: Path) -> Path:
        if path.is_absolute():
            return path
        return (PROJECT_ROOT / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
