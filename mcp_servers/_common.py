from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from backend.app.connectors.registry import get_datasource_manager
from backend.app.core.db import get_sqlite_engine
from backend.app.metadata.models import create_metadata_schema


class DatasourceUnavailable(ValueError):
    def __init__(self, datasource: str | None, message: str) -> None:
        super().__init__(message)
        self.datasource = datasource
        self.message = message


def ok(data: Any) -> dict:
    return {"ok": True, "data": to_jsonable(data)}


def err(kind: str, message: str, detail: dict | None = None) -> dict:
    payload = {"kind": kind, "message": message}
    if detail is not None:
        payload["detail"] = to_jsonable(detail)
    return {"ok": False, "error": payload}


def ensure_ready() -> None:
    create_metadata_schema(get_sqlite_engine())


def resolve_datasource(datasource: str | None = None) -> str:
    manager = get_datasource_manager()
    name = datasource or manager.default_name
    if not name:
        raise DatasourceUnavailable(datasource, "No datasource has been registered.")
    try:
        manager.get(name)
    except (KeyError, LookupError) as exc:
        raise DatasourceUnavailable(name, str(exc)) from exc
    return name


def datasource_error(exc: DatasourceUnavailable) -> dict:
    return err(
        "datasource_unavailable",
        exc.message,
        {"datasource": exc.datasource},
    )


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((to_jsonable(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
