import os

import pytest


TEST_ENV_DEFAULTS = {
    "DEEPSEEK_API_KEY": "",
    "LLM_PROVIDER": "mock",
    "SEMANTIC_GUARD_MODE": "off",
    "AUTH_ENABLED": "false",
    "CORS_ALLOW_ORIGINS": (
        "http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175,"
        "http://localhost:5173,http://localhost:5174,http://localhost:5175"
    ),
}

for _key, _value in TEST_ENV_DEFAULTS.items():
    os.environ[_key] = _value

from backend.app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_runtime_settings(monkeypatch):
    """Keep developer .env choices from changing unit-test behavior."""
    for key, value in TEST_ENV_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
