from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Any

from backend.app.config import DEFAULT_LOCALE, default_locale, supported_locales

logger = logging.getLogger(__name__)
CATALOG_DIR = Path(__file__).resolve().parent


def resolve_locale(locale: str | None = None, accept_language: str | None = None) -> str:
    requested = _normalize_locale(locale)
    if requested:
        return requested

    for item in (accept_language or "").split(","):
        language = item.split(";", 1)[0].strip()
        resolved = _normalize_locale(language)
        if resolved:
            return resolved

    return default_locale()


def t(key: str, locale: str | None = None, **params: Any) -> str:
    active_locale = resolve_locale(locale)
    template = _catalog(active_locale).get(key)
    if template is None:
        template = _catalog(DEFAULT_LOCALE).get(key)
        if template is None:
            logger.warning("Missing i18n key: %s", key)
            return key
        logger.warning("Missing i18n key %s for locale %s; falling back to %s.", key, active_locale, DEFAULT_LOCALE)
    try:
        return template.format(**_safe_params(template, params))
    except Exception:
        logger.warning("Failed to format i18n key %s for locale %s.", key, active_locale, exc_info=True)
        return template


def _normalize_locale(locale: str | None) -> str | None:
    if not locale:
        return None
    normalized = locale.strip().replace("_", "-").casefold()
    if not normalized:
        return None
    language = normalized.split("-", 1)[0]
    return language if language in supported_locales() else None


def _safe_params(template: str, params: dict[str, Any]) -> dict[str, Any]:
    names = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    return {name: params.get(name, "") for name in names}


@lru_cache
def _catalog(locale: str) -> dict[str, str]:
    path = CATALOG_DIR / f"{locale}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Missing i18n catalog: %s", path)
        return {}
    if not isinstance(payload, dict):
        logger.warning("Invalid i18n catalog payload: %s", path)
        return {}
    return {str(key): str(value) for key, value in payload.items()}
