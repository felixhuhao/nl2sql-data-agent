from types import SimpleNamespace

from backend.app.config import (
    DEFAULT_BROWSE_LIMIT,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RANKING_LIMIT,
    Settings,
    deepseek_config_available,
    embedding_model_name,
    effective_llm_provider_name,
    llm_provider_mode,
    vector_config_allows_attempt,
    vector_enabled_mode,
)


def _clear_config_env(monkeypatch):
    for key in (
        "DEEPSEEK_API_KEY",
        "LLM_PROVIDER",
        "VECTOR_ENABLED",
        "EMBEDDING_MODEL",
        "SQL_DEFAULT_RANKING_LIMIT",
        "SQL_DEFAULT_BROWSE_LIMIT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_llm_provider_defaults_to_auto_without_deepseek_key(monkeypatch):
    _clear_config_env(monkeypatch)
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "auto"
    assert llm_provider_mode(settings) == "auto"
    assert deepseek_config_available(settings) is False
    assert effective_llm_provider_name(settings) == "mock"


def test_llm_provider_auto_prefers_deepseek_when_key_is_configured(monkeypatch):
    _clear_config_env(monkeypatch)
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    assert llm_provider_mode(settings) == "auto"
    assert deepseek_config_available(settings) is True
    assert effective_llm_provider_name(settings) == "deepseek"


def test_explicit_llm_provider_mode_is_effective_provider():
    assert effective_llm_provider_name(SimpleNamespace(llm_provider="mock", deepseek_api_key="test-key")) == "mock"
    assert effective_llm_provider_name(SimpleNamespace(llm_provider="deepseek", deepseek_api_key=None)) == "deepseek"


def test_sql_generation_limits_default_and_can_be_overridden(monkeypatch):
    _clear_config_env(monkeypatch)
    settings = Settings(_env_file=None)

    assert settings.sql_default_ranking_limit == DEFAULT_RANKING_LIMIT
    assert settings.sql_default_browse_limit == DEFAULT_BROWSE_LIMIT

    custom_settings = Settings(
        _env_file=None,
        sql_default_ranking_limit=15,
        sql_default_browse_limit=25,
    )

    assert custom_settings.sql_default_ranking_limit == 15
    assert custom_settings.sql_default_browse_limit == 25


def test_vector_enabled_defaults_to_auto(monkeypatch):
    _clear_config_env(monkeypatch)
    settings = Settings(_env_file=None)

    assert settings.vector_enabled == "auto"
    assert vector_enabled_mode(settings) == "auto"
    assert vector_config_allows_attempt(settings) is True


def test_embedding_model_defaults_when_blank_or_missing(monkeypatch):
    _clear_config_env(monkeypatch)
    assert embedding_model_name(Settings(_env_file=None)) == DEFAULT_EMBEDDING_MODEL
    assert embedding_model_name(SimpleNamespace(embedding_model="")) == DEFAULT_EMBEDDING_MODEL
    assert embedding_model_name(SimpleNamespace(embedding_model=None)) == DEFAULT_EMBEDDING_MODEL
    assert embedding_model_name(SimpleNamespace(embedding_model=" custom-model ")) == "custom-model"


def test_vector_enabled_mode_accepts_legacy_boolean_values():
    assert vector_enabled_mode(Settings(_env_file=None, vector_enabled=True)) == "enabled"
    assert vector_enabled_mode(Settings(_env_file=None, vector_enabled=False)) == "disabled"
    assert vector_enabled_mode(SimpleNamespace(vector_enabled=True)) == "enabled"
    assert vector_enabled_mode(SimpleNamespace(vector_enabled=False)) == "disabled"


def test_vector_enabled_mode_accepts_string_values():
    assert vector_enabled_mode(SimpleNamespace(vector_enabled="true")) == "enabled"
    assert vector_enabled_mode(SimpleNamespace(vector_enabled="false")) == "disabled"
    assert vector_enabled_mode(SimpleNamespace(vector_enabled="auto")) == "auto"
    assert vector_enabled_mode(SimpleNamespace(vector_enabled="")) == "auto"
