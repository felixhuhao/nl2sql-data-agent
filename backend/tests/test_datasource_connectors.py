from pathlib import Path

import duckdb
import pytest

from backend.app.config import Settings
from backend.app.connectors.duckdb import DuckDBConnector
from backend.app.connectors.manager import DataSourceManager
from backend.app.connectors.registry import create_datasource_manager, get_datasource_dialect


def test_duckdb_connector_executes_readonly_query(tmp_path: Path):
    db_path = tmp_path / "ecommerce.duckdb"
    _create_orders_db(db_path)
    connector = DuckDBConnector(_settings(tmp_path, db_path))

    result = connector.execute("SELECT id, amount FROM orders ORDER BY id")

    assert result.columns == ["id", "amount"]
    assert result.rows == [[1, 10], [2, 20]]
    assert result.row_count == 2


def test_duckdb_connector_explain_returns_text_plan(tmp_path: Path):
    db_path = tmp_path / "ecommerce.duckdb"
    _create_orders_db(db_path)
    connector = DuckDBConnector(_settings(tmp_path, db_path))

    explain = connector.explain("SELECT id, amount FROM orders WHERE amount > 10")

    assert explain is not None
    assert explain["dialect"] == "duckdb"
    assert explain["format"] == "text"
    assert any("SEQ_SCAN" in line or "Sequential Scan" in line for line in explain["lines"])
    assert any("orders" in line for line in explain["lines"])


def test_duckdb_connector_sync_schema_returns_snapshot(tmp_path: Path):
    db_path = tmp_path / "ecommerce.duckdb"
    _create_orders_db(db_path)
    connector = DuckDBConnector(_settings(tmp_path, db_path))

    snapshot = connector.sync_schema()

    assert snapshot.datasource_name == "duckdb_ecommerce"
    assert len(snapshot.tables) == 1
    table = snapshot.tables[0]
    assert table.name == "orders"
    assert table.row_count == 2
    assert [column.name for column in table.columns] == ["id", "amount"]
    assert table.columns[0].sample_values == ["1", "2"]


def test_duckdb_connector_readonly_missing_file_raises(tmp_path: Path):
    connector = DuckDBConnector(_settings(tmp_path, tmp_path / "missing.duckdb"))

    with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
        connector.get_connection(read_only=True)


def test_datasource_manager_registers_and_lists_sources(tmp_path: Path):
    connector = DuckDBConnector(_settings(tmp_path, tmp_path / "ecommerce.duckdb"))
    manager = DataSourceManager(default_name="missing_default")

    manager.register(connector)

    assert manager.get("duckdb_ecommerce") is connector
    assert manager.get_default() is connector
    assert manager.default_name == "duckdb_ecommerce"
    assert manager.list_sources()[0].name == "duckdb_ecommerce"


def test_datasource_manager_rejects_duplicate_names(tmp_path: Path):
    connector = DuckDBConnector(_settings(tmp_path, tmp_path / "ecommerce.duckdb"))
    manager = DataSourceManager()
    manager.register(connector)

    with pytest.raises(ValueError, match="already registered"):
        manager.register(connector)


def test_datasource_manager_get_unknown_raises():
    manager = DataSourceManager()

    with pytest.raises(KeyError, match="Unknown datasource"):
        manager.get("nonexistent")


def test_datasource_manager_get_default_empty_raises():
    manager = DataSourceManager()

    with pytest.raises(LookupError, match="No datasource"):
        manager.get_default()


def test_create_datasource_manager_registers_duckdb(tmp_path: Path):
    settings = _settings(tmp_path, tmp_path / "ecommerce.duckdb")

    manager = create_datasource_manager(settings)

    assert manager.default_name == "duckdb_ecommerce"
    assert [source.name for source in manager.list_sources()] == ["duckdb_ecommerce"]


def test_get_datasource_dialect_falls_back_for_missing_sources():
    assert get_datasource_dialect("clickhouse_analytics") == "clickhouse"
    assert get_datasource_dialect("unknown") == "duckdb"


def _settings(tmp_path: Path, db_path: Path) -> Settings:
    return Settings(
        duckdb_path=db_path,
        sqlite_path=tmp_path / "metadata.sqlite",
        clickhouse_enabled=False,
    )


def _create_orders_db(db_path: Path) -> None:
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("CREATE TABLE orders (id INTEGER NOT NULL, amount INTEGER)")
        connection.execute("INSERT INTO orders VALUES (1, 10), (2, 20)")
    finally:
        connection.close()
