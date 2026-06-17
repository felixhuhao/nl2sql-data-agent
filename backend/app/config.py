from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_RANKING_LIMIT = 10
DEFAULT_BROWSE_LIMIT = 20


class Settings(BaseSettings):
    app_env: str = "local"
    duckdb_path: Path = Field(default=PROJECT_ROOT / "data" / "ecommerce.duckdb")
    sqlite_path: Path = Field(default=PROJECT_ROOT / "data" / "metadata.sqlite")
    dataset_current_date: str = "2025-12-31"
    semantic_overlay_path: Path | None = None
    llm_provider: str = "auto"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_timeout: float = 30.0
    semantic_guard_mode: str = "off"
    semantic_guard_timeout: float = 30.0
    sql_default_ranking_limit: int = Field(default=DEFAULT_RANKING_LIMIT, ge=1, le=500)
    sql_default_browse_limit: int = Field(default=DEFAULT_BROWSE_LIMIT, ge=1, le=500)
    vector_enabled: str | bool = "auto"
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
    nl2sql_mcp_service_token: str = ""

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


def llm_provider_mode(settings: Any | None = None) -> str:
    value = str(getattr(settings or get_settings(), "llm_provider", "auto") or "auto").strip().casefold()
    return value or "auto"


def deepseek_config_available(settings: Any | None = None) -> bool:
    return bool(getattr(settings or get_settings(), "deepseek_api_key", None))


def effective_llm_provider_name(settings: Any | None = None) -> str:
    settings = settings or get_settings()
    provider_mode = llm_provider_mode(settings)
    if provider_mode == "auto":
        return "deepseek" if deepseek_config_available(settings) else "mock"
    return provider_mode


def semantic_guard_mode(settings: Any | None = None) -> str:
    value = str(getattr(settings or get_settings(), "semantic_guard_mode", "off") or "off").strip().casefold()
    return value if value in {"off", "warn", "enforce"} else "off"


def vector_enabled_mode(settings: Any | None = None) -> str:
    raw_value = getattr(settings or get_settings(), "vector_enabled", "auto")
    if isinstance(raw_value, bool):
        return "enabled" if raw_value else "disabled"
    value = str(raw_value or "auto").strip().casefold()
    if value in {"1", "true", "yes", "on", "enabled", "enable"}:
        return "enabled"
    if value in {"0", "false", "no", "off", "disabled", "disable"}:
        return "disabled"
    return "auto"


def vector_config_allows_attempt(settings: Any | None = None) -> bool:
    return vector_enabled_mode(settings) != "disabled"


def embedding_model_name(settings: Any | None = None) -> str:
    raw_value = getattr(settings or get_settings(), "embedding_model", None)
    value = str(raw_value or "").strip()
    return value or DEFAULT_EMBEDDING_MODEL
