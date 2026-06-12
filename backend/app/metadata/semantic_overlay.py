from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence, Set
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.app.config import PROJECT_ROOT, get_settings


DEFAULT_OVERLAY_PATH = Path(__file__).with_name("semantic_overlays") / "ecommerce.yml"
_UNSET = object()
TableSemantics = Mapping[str, tuple[str, str, str]]
ColumnSemantics = Mapping[str, str]
TableColumnSemantics = Mapping[tuple[str, str], str]
SampleValueFallbacks = Mapping[str, list[str]]
ConfirmedRelationships = Sequence[tuple[str, str, str, str, str, str]]


@dataclass(frozen=True)
class SemanticOverlay:
    table_semantics: TableSemantics
    column_semantics: ColumnSemantics
    table_column_semantics: TableColumnSemantics
    dimension_columns: Set[str]
    metric_columns: Set[str]
    sample_value_fallbacks: SampleValueFallbacks
    confirmed_relationships: ConfirmedRelationships


def _overlay_path() -> Path:
    configured = get_settings().semantic_overlay_path
    if configured:
        path = configured.expanduser()
        if path.is_absolute():
            return path
        return (PROJECT_ROOT / path).resolve()
    return DEFAULT_OVERLAY_PATH


def _resolve_overlay_path(path: str | Path | None = None) -> Path:
    overlay_path = Path(path).expanduser() if path is not None else _overlay_path()
    if overlay_path.is_absolute():
        return overlay_path.resolve()
    return (PROJECT_ROOT / overlay_path).resolve()


def _load_overlay(path: str | Path | None = None) -> dict[str, Any]:
    return _load_overlay_by_path(str(_resolve_overlay_path(path)))


@lru_cache(maxsize=4)
def _load_overlay_by_path(path: str) -> dict[str, Any]:
    import yaml

    overlay_path = Path(path)
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


def load_semantic_overlay(path: str | Path | None = None) -> SemanticOverlay:
    data = _load_overlay(path)
    return SemanticOverlay(
        table_semantics=_table_semantics(data),
        column_semantics=_column_semantics(data),
        table_column_semantics=_table_column_semantics(data),
        dimension_columns=_string_set(data, "dimension_columns"),
        metric_columns=_string_set(data, "metric_columns"),
        sample_value_fallbacks=_sample_value_fallbacks(data),
        confirmed_relationships=_confirmed_relationships(data),
    )


@lru_cache(maxsize=4)
def _semantic_overlay(path: str | None = None) -> SemanticOverlay:
    return load_semantic_overlay(path)


def _default_overlay() -> SemanticOverlay:
    return _semantic_overlay(str(_resolve_overlay_path()))


class _LazyValue:
    """Lazily expose overlay-backed constants while preserving constant-like identity.

    Tests or reload paths that need a different overlay should call
    _clear_semantic_overlay_caches().
    """

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._result: Any = _UNSET

    def _get(self) -> Any:
        if self._result is _UNSET:
            self._result = self._factory()
        return self._result

    def _reset(self) -> None:
        self._result = _UNSET


class _LazyMapping(Mapping):
    def __init__(self, factory: Callable[[], Mapping]) -> None:
        self._value = _LazyValue(factory)

    def __getitem__(self, key: object) -> object:
        return self._value._get()[key]

    def __iter__(self) -> Iterator:
        return iter(self._value._get())

    def __len__(self) -> int:
        return len(self._value._get())

    def __repr__(self) -> str:
        return repr(self._value._get())

    def _reset(self) -> None:
        self._value._reset()


class _LazySet(Set):
    def __init__(self, factory: Callable[[], Set]) -> None:
        self._value = _LazyValue(factory)

    def __contains__(self, value: object) -> bool:
        return value in self._value._get()

    def __iter__(self) -> Iterator:
        return iter(self._value._get())

    def __len__(self) -> int:
        return len(self._value._get())

    def __repr__(self) -> str:
        return repr(self._value._get())

    def _reset(self) -> None:
        self._value._reset()


class _LazySequence(Sequence):
    def __init__(self, factory: Callable[[], Sequence]) -> None:
        self._value = _LazyValue(factory)

    def __getitem__(self, index: int | slice) -> object:
        return self._value._get()[index]

    def __contains__(self, value: object) -> bool:
        return value in self._value._get()

    def __len__(self) -> int:
        return len(self._value._get())

    def __repr__(self) -> str:
        return repr(self._value._get())

    def _reset(self) -> None:
        self._value._reset()


TABLE_SEMANTICS = _LazyMapping(lambda: _default_overlay().table_semantics)
COLUMN_SEMANTICS = _LazyMapping(lambda: _default_overlay().column_semantics)
TABLE_COLUMN_SEMANTICS = _LazyMapping(lambda: _default_overlay().table_column_semantics)
DIMENSION_COLUMNS = _LazySet(lambda: _default_overlay().dimension_columns)
METRIC_COLUMNS = _LazySet(lambda: _default_overlay().metric_columns)
SAMPLE_VALUE_FALLBACKS = _LazyMapping(lambda: _default_overlay().sample_value_fallbacks)
CONFIRMED_RELATIONSHIPS = _LazySequence(lambda: _default_overlay().confirmed_relationships)


def _reset_lazy_overlays() -> None:
    for value in (
        TABLE_SEMANTICS,
        COLUMN_SEMANTICS,
        TABLE_COLUMN_SEMANTICS,
        DIMENSION_COLUMNS,
        METRIC_COLUMNS,
        SAMPLE_VALUE_FALLBACKS,
        CONFIRMED_RELATIONSHIPS,
    ):
        value._reset()


def _clear_semantic_overlay_caches() -> None:
    _load_overlay_by_path.cache_clear()
    _semantic_overlay.cache_clear()
    _reset_lazy_overlays()


def sample_value_fallbacks_json(column_name: str) -> str | None:
    values = SAMPLE_VALUE_FALLBACKS.get(column_name)
    return json.dumps(values, ensure_ascii=False) if values else None
