import os

import pytest


TEST_ENV_DEFAULTS = {
    "DEEPSEEK_API_KEY": "",
    "LLM_PROVIDER": "mock",
    "SEMANTIC_GUARD_MODE": "off",
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
