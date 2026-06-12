from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.app.config import PROJECT_ROOT, get_settings


DEFAULT_OVERLAY_PATH = Path(__file__).with_name("semantic_overlays") / "ecommerce.yml"


def _overlay_path() -> Path:
    configured = get_settings().semantic_overlay_path
    if configured:
        path = configured.expanduser()
        if path.is_absolute():
            return path
        return (PROJECT_ROOT / path).resolve()
    return DEFAULT_OVERLAY_PATH


@lru_cache(maxsize=4)
def _load_overlay(path: str | None = None) -> dict[str, Any]:
    overlay_path = Path(path) if path else _overlay_path()
    with overlay_path.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Semantic overlay must be a mapping: {overlay_path}")
    return data


def _table_semantics(data: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
    tables = data.get("tables") or {}
    return {
        name: (
            str(values.get("display_name") or name),
            str(values.get("description") or ""),
            str(values.get("domain") or ""),
        )
        for name, values in tables.items()
        if isinstance(values, dict)
    }


def _column_semantics(data: dict[str, Any]) -> dict[str, str]:
    columns = data.get("columns") or {}
    return {str(name): str(description) for name, description in columns.items()}


def _table_column_semantics(data: dict[str, Any]) -> dict[tuple[str, str], str]:
    table_columns = data.get("table_columns") or {}
    semantics: dict[tuple[str, str], str] = {}
    for table_name, columns in table_columns.items():
        if not isinstance(columns, dict):
            continue
        for column_name, description in columns.items():
            semantics[(str(table_name), str(column_name))] = str(description)
    return semantics


def _string_set(data: dict[str, Any], key: str) -> set[str]:
    values = data.get(key) or []
    return {str(value) for value in values}


def _sample_value_fallbacks(data: dict[str, Any]) -> dict[str, list[str]]:
    fallbacks = data.get("sample_value_fallbacks") or {}
    return {
        str(column_name): [str(value) for value in values]
        for column_name, values in fallbacks.items()
        if isinstance(values, list)
    }


def _confirmed_relationships(data: dict[str, Any]) -> list[tuple[str, str, str, str, str, str]]:
    relationships = data.get("confirmed_relationships") or []
    parsed = []
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        parsed.append(
            (
                str(relationship.get("source_table") or ""),
                str(relationship.get("source_column") or ""),
                str(relationship.get("target_table") or ""),
                str(relationship.get("target_column") or ""),
                str(relationship.get("relationship_type") or ""),
                str(relationship.get("description") or ""),
            )
        )
    return parsed


_OVERLAY = _load_overlay()

TABLE_SEMANTICS = _table_semantics(_OVERLAY)
COLUMN_SEMANTICS = _column_semantics(_OVERLAY)
TABLE_COLUMN_SEMANTICS = _table_column_semantics(_OVERLAY)
DIMENSION_COLUMNS = _string_set(_OVERLAY, "dimension_columns")
METRIC_COLUMNS = _string_set(_OVERLAY, "metric_columns")
SAMPLE_VALUE_FALLBACKS = _sample_value_fallbacks(_OVERLAY)
CONFIRMED_RELATIONSHIPS = _confirmed_relationships(_OVERLAY)


def sample_value_fallbacks_json(column_name: str) -> str | None:
    values = SAMPLE_VALUE_FALLBACKS.get(column_name)
    return json.dumps(values, ensure_ascii=False) if values else None
