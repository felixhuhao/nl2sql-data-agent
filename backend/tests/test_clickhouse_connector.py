from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.connectors.clickhouse import ClickHouseConnector, _base_type, _quote_identifier
from backend.app.connectors import registry


def test_clickhouse_connector_builds_client_with_safety_settings(monkeypatch, tmp_path: Path):
    fake_module = FakeClickHouseModule(FakeClient())
    monkeypatch.setattr("backend.app.connectors.clickhouse._load_clickhouse_connect", lambda: fake_module)

    connector = ClickHouseConnector(_settings(tmp_path))
    client = connector.get_connection(read_only=True)

    assert client is fake_module.client
    assert fake_module.kwargs["host"] == "ch.local"
    assert fake_module.kwargs["port"] == 8123
    assert fake_module.kwargs["database"] == "ecommerce"
    assert fake_module.kwargs["settings"] == {
        "max_execution_time": 7,
        "max_result_rows": 123,
        "readonly": 1,
    }


def test_clickhouse_connector_execute_returns_raw_result(monkeypatch, tmp_path: Path):
    client = FakeClient()
    client.results.append(FakeResult(rows=[("华东", 10), ("华南", 8)], columns=["region_group", "sales_amount"]))
    monkeypatch.setattr("backend.app.connectors.clickhouse._load_clickhouse_connect", lambda: FakeClickHouseModule(client))

    result = ClickHouseConnector(_settings(tmp_path)).execute("SELECT 1", timeout=3)

    assert result.columns == ["region_group", "sales_amount"]
    assert result.rows == [["华东", 10], ["华南", 8]]
    assert result.row_count == 2
    assert client.queries[0]["settings"] == {"max_execution_time": 3}
    assert client.closed is True


def test_clickhouse_connector_explain_returns_index_plan(monkeypatch, tmp_path: Path):
    client = FakeClient()
    fake_module = FakeClickHouseModule(client)
    client.results.append(
        FakeResult(
            rows=[
                ("ReadFromMergeTree (Parts: 3/24)",),
                ("JoiningTransform",),
            ],
            columns=["explain"],
        )
    )
    monkeypatch.setattr("backend.app.connectors.clickhouse._load_clickhouse_connect", lambda: fake_module)

    result = ClickHouseConnector(_settings(tmp_path)).explain("SELECT * FROM fact_orders")

    assert client.queries[0]["sql"] == "EXPLAIN indexes=1 SELECT * FROM fact_orders"
    assert fake_module.get_client_calls == 1
    assert result == {
        "format": "PLAN indexes=1",
        "lines": ["ReadFromMergeTree (Parts: 3/24)", "JoiningTransform"],
        "text": "ReadFromMergeTree (Parts: 3/24)\nJoiningTransform",
    }
    assert client.closed is True


def test_clickhouse_connector_execute_with_explain_reuses_client(monkeypatch, tmp_path: Path):
    client = FakeClient()
    fake_module = FakeClickHouseModule(client)
    client.results.extend(
        [
            FakeResult(rows=[("华东", 10)], columns=["region_group", "sales_amount"]),
            FakeResult(rows=[("HashJoin",), ("ReadFromMergeTree Parts: 1/12",)], columns=["explain"]),
        ]
    )
    monkeypatch.setattr("backend.app.connectors.clickhouse._load_clickhouse_connect", lambda: fake_module)

    raw_result, explain_result = ClickHouseConnector(_settings(tmp_path)).execute_with_explain("SELECT 1", timeout=3)

    assert fake_module.get_client_calls == 1
    assert [query["sql"] for query in client.queries] == [
        "SELECT 1",
        "EXPLAIN indexes=1 SELECT 1",
    ]
    assert client.queries[0]["settings"] == {"max_execution_time": 3}
    assert client.queries[1]["settings"] is None
    assert raw_result.columns == ["region_group", "sales_amount"]
    assert explain_result == {
        "format": "PLAN indexes=1",
        "lines": ["HashJoin", "ReadFromMergeTree Parts: 1/12"],
        "text": "HashJoin\nReadFromMergeTree Parts: 1/12",
    }
    assert client.closed is True


def test_clickhouse_connector_ping_closes_client(monkeypatch, tmp_path: Path):
    client = FakeClient()
    client.results.append(FakeResult(rows=[(1,)]))
    monkeypatch.setattr("backend.app.connectors.clickhouse._load_clickhouse_connect", lambda: FakeClickHouseModule(client))

    ClickHouseConnector(_settings(tmp_path)).ping()

    assert client.queries[0]["sql"] == "SELECT 1"
    assert client.closed is True


def test_clickhouse_connector_sync_schema_returns_snapshot(monkeypatch, tmp_path: Path):
    client = FakeClient()
    client.results.extend(
        [
            FakeResult(
                rows=[
                    ("dim_regions", "MergeTree", 30, "", "region_key"),
                    ("fact_orders", "MergeTree", 100, "intDiv(date_key, 100)", "date_key, region_key"),
                ]
            ),
            FakeResult(
                rows=[
                    ("dim_regions", "region_group", "LowCardinality(String)", 0, 0, 0),
                    ("fact_orders", "date_key", "UInt32", 1, 1, 1),
                    ("fact_orders", "payment_amount", "Nullable(Decimal(12, 2))", 0, 0, 0),
                ]
            ),
            FakeResult(rows=[("华东",), ("华南",)]),
            FakeResult(rows=[(20251231,), (20251230,)]),
            FakeResult(rows=[("10.00",), ("20.00",)]),
        ]
    )
    monkeypatch.setattr("backend.app.connectors.clickhouse._load_clickhouse_connect", lambda: FakeClickHouseModule(client))

    snapshot = ClickHouseConnector(_settings(tmp_path)).sync_schema()

    assert snapshot.datasource_name == "clickhouse_ecommerce"
    assert [table.name for table in snapshot.tables] == ["dim_regions", "fact_orders"]
    assert snapshot.tables[0].engine == "MergeTree"
    assert snapshot.tables[1].partition_key == "intDiv(date_key, 100)"
    assert snapshot.tables[0].columns[0].low_cardinality is True
    assert snapshot.tables[1].columns[0].is_partition_key is True
    assert snapshot.tables[1].columns[1].nullable is True
    assert snapshot.tables[1].columns[1].data_type == "Decimal(12, 2)"
    assert client.closed is True


def test_clickhouse_connector_identifier_validation():
    assert _quote_identifier("fact_orders") == "`fact_orders`"
    with pytest.raises(ValueError, match="Invalid ClickHouse identifier"):
        _quote_identifier("fact-orders")


def test_clickhouse_connector_unwraps_type_wrappers():
    assert _base_type("LowCardinality(Nullable(String))") == "String"
    assert _base_type("Nullable(Decimal(12, 2))") == "Decimal(12, 2)"


def test_registry_registers_clickhouse_when_enabled(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(registry, "ClickHouseConnector", FakeRegisteredClickHouseConnector)
    settings = _settings(tmp_path, clickhouse_enabled=True)

    manager = registry.create_datasource_manager(settings)

    assert [source.name for source in manager.list_sources()] == ["duckdb_ecommerce", "clickhouse_ecommerce"]


def test_registry_skips_clickhouse_when_ping_fails(monkeypatch, tmp_path: Path, caplog):
    monkeypatch.setattr(registry, "ClickHouseConnector", FailingRegisteredClickHouseConnector)
    settings = _settings(tmp_path, clickhouse_enabled=True)

    manager = registry.create_datasource_manager(settings)

    assert [source.name for source in manager.list_sources()] == ["duckdb_ecommerce"]
    assert "ClickHouse connection failed" in caplog.text


class FakeResult:
    def __init__(self, rows=None, columns=None):
        self.result_rows = rows or []
        self.column_names = columns or []


class FakeClient:
    def __init__(self):
        self.results = []
        self.queries = []
        self.closed = False

    def query(self, sql, **kwargs):
        self.queries.append({"sql": sql, **kwargs})
        return self.results.pop(0) if self.results else FakeResult()

    def close(self):
        self.closed = True


class FakeClickHouseModule:
    def __init__(self, client):
        self.client = client
        self.kwargs = {}
        self.get_client_calls = 0

    def get_client(self, **kwargs):
        self.get_client_calls += 1
        self.kwargs = kwargs
        return self.client


class FakeRegisteredClickHouseConnector:
    name = "clickhouse_ecommerce"
    dialect = "clickhouse"
    display_name = "ClickHouse (OLAP)"

    def __init__(self, settings):
        self.settings = settings

    def ping(self):
        return None

    def get_connection(self, read_only=True):
        return None

    def sync_schema(self):
        raise NotImplementedError

    def execute(self, sql, timeout=None):
        raise NotImplementedError

    def close(self):
        return None


class FailingRegisteredClickHouseConnector(FakeRegisteredClickHouseConnector):
    def ping(self):
        raise ConnectionError("not reachable")


def _settings(tmp_path: Path, clickhouse_enabled: bool = False) -> Settings:
    return Settings(
        duckdb_path=tmp_path / "ecommerce.duckdb",
        sqlite_path=tmp_path / "metadata.sqlite",
        default_datasource="duckdb_ecommerce",
        clickhouse_enabled=clickhouse_enabled,
        clickhouse_host="ch.local",
        clickhouse_port=8123,
        clickhouse_user="default",
        clickhouse_password="",
        clickhouse_database="ecommerce",
        clickhouse_max_execution_time=7,
        clickhouse_max_result_rows=123,
    )
