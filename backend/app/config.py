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
    nl2sql_mcp_allowed_hosts: str = (
        "127.0.0.1:8000,localhost:8000,backend:8000,nl2sql_pro-backend-1:8000,"
        "host.docker.internal:8000,172.17.0.1:8000,172.18.0.1:8000,172.22.0.1:8000"
    )
    nl2sql_mcp_allowed_origins: str = ""
    auth_enabled: bool = False
    auth_cookie_name: str = "nl2sql_session"
    auth_cookie_secure: bool = True
    auth_session_ttl_seconds: int = Field(default=28800, gt=0)
    auth_sqlite_path: Path = Field(default=PROJECT_ROOT / "data" / "auth.sqlite")
    auth_bootstrap_username: str = "admin"
    auth_bootstrap_password: str = ""
    auth_login_max_attempts: int = Field(default=5, gt=0)
    auth_login_window_seconds: int = Field(default=60, gt=0)
    auth_login_lockout_seconds: int = Field(default=300, gt=0)
    cors_allow_origins: str = (
        "http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175,"
        "http://localhost:5173,http://localhost:5174,http://localhost:5175"
    )

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / "backend" / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def resolved_duckdb_path(self) -> Path:
        return self._resolve_path(self.duckdb_path)

    def resolved_sqlite_path(self) -> Path:
        return self._resolve_path(self.sqlite_path)

    def resolved_auth_sqlite_path(self) -> Path:
        return self._resolve_path(self.auth_sqlite_path)

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


def cors_allow_origins(settings: Any | None = None) -> list[str]:
    raw_value = str(getattr(settings or get_settings(), "cors_allow_origins", "") or "")
    return [origin.strip() for origin in raw_value.split(",") if origin.strip()]


def nl2sql_mcp_allowed_hosts(settings: Any | None = None) -> list[str]:
    raw_value = str(getattr(settings or get_settings(), "nl2sql_mcp_allowed_hosts", "") or "")
    return [host.strip() for host in raw_value.split(",") if host.strip()]


def nl2sql_mcp_allowed_origins(settings: Any | None = None) -> list[str]:
    raw_value = str(getattr(settings or get_settings(), "nl2sql_mcp_allowed_origins", "") or "")
    return [origin.strip() for origin in raw_value.split(",") if origin.strip()]


def embedding_model_name(settings: Any | None = None) -> str:
    raw_value = getattr(settings or get_settings(), "embedding_model", None)
    value = str(raw_value or "").strip()
    return value or DEFAULT_EMBEDDING_MODEL
