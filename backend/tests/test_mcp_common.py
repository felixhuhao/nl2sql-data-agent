from datetime import date, datetime
from decimal import Decimal

import pytest

from mcp_servers import _common


def test_ok_and_err_return_json_safe_envelopes():
    assert _common.ok(
        {
            "decimal": Decimal("12.50"),
            "date": date(2025, 12, 31),
            "datetime": datetime(2025, 12, 31, 1, 2, 3),
            "items": {Decimal("1.5")},
        }
    ) == {
        "ok": True,
        "data": {
            "decimal": 12.5,
            "date": "2025-12-31",
            "datetime": "2025-12-31T01:02:03",
            "items": [1.5],
        },
    }

    assert _common.err("not_found", "Missing", {"table": "orders"}) == {
        "ok": False,
        "error": {
            "kind": "not_found",
            "message": "Missing",
            "detail": {"table": "orders"},
        },
    }


def test_err_preserves_empty_detail_and_sets_are_deterministic():
    assert _common.err("internal", "Empty detail", {}) == {
        "ok": False,
        "error": {
            "kind": "internal",
            "message": "Empty detail",
            "detail": {},
        },
    }

    assert _common.to_jsonable({"values": {"b", "a", "c"}}) == {"values": ["a", "b", "c"]}


def test_resolve_datasource_uses_manager_default(monkeypatch):
    class FakeManager:
        default_name = "duckdb_ecommerce"

        def get(self, name: str):
            assert name == "duckdb_ecommerce"
            return object()

    monkeypatch.setattr(_common, "get_datasource_manager", lambda: FakeManager())

    assert _common.resolve_datasource(None) == "duckdb_ecommerce"


def test_resolve_datasource_rejects_unknown(monkeypatch):
    class FakeManager:
        default_name = "duckdb_ecommerce"

        def get(self, name: str):
            raise KeyError(f"Unknown datasource: {name}")

    monkeypatch.setattr(_common, "get_datasource_manager", lambda: FakeManager())

    with pytest.raises(_common.DatasourceUnavailable, match="Unknown datasource"):
        _common.resolve_datasource("missing")
