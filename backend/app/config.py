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
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"

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
