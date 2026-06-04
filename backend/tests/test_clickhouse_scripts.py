from pathlib import Path

import duckdb

from backend.app.config import Settings
from scripts import export_ecommerce_csv, seed_clickhouse


def test_export_tables_writes_csv_with_header(tmp_path: Path):
    duckdb_path = tmp_path / "ecommerce.duckdb"
    with duckdb.connect(str(duckdb_path)) as connection:
        connection.execute("CREATE TABLE orders (id INTEGER, amount INTEGER)")
        connection.execute("INSERT INTO orders VALUES (1, 10), (2, 20)")

    counts = export_ecommerce_csv.export_tables(
        duckdb_path=duckdb_path,
        output_dir=tmp_path / "csv",
        tables=["orders"],
    )

    assert counts == {"orders": 2}
    assert (tmp_path / "csv" / "orders.csv").read_text(encoding="utf-8").splitlines() == [
        "id,amount",
        "1,10",
        "2,20",
    ]


def test_seed_clickhouse_runs_schema_insert_and_count(monkeypatch, tmp_path: Path):
    input_dir = tmp_path / "csv"
    input_dir.mkdir()
    (input_dir / "orders.csv").write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text("CREATE DATABASE ecommerce; CREATE TABLE ecommerce.orders (id UInt32);", encoding="utf-8")
    calls = []

    def fake_post(settings, query, data=None):
        calls.append({"query": query, "data": data})
        if query.startswith("SELECT count()"):
            return b"2"
        return b""

    monkeypatch.setattr(seed_clickhouse, "TABLES", ["orders"])
    monkeypatch.setattr(seed_clickhouse, "_post_clickhouse", fake_post)

    result = seed_clickhouse.seed_clickhouse(
        settings=_settings(tmp_path),
        input_dir=input_dir,
        schema_file=schema_file,
        reset_schema=True,
    )

    assert result == {"orders": 2}
    assert calls[0]["query"] == "CREATE DATABASE ecommerce"
    assert calls[1]["query"] == "CREATE TABLE ecommerce.orders (id UInt32)"
    assert calls[2]["query"] == "INSERT INTO `ecommerce`.`orders` FORMAT CSVWithNames"
    assert calls[2]["data"] == b"id,amount\n1,10\n2,20\n"
    assert calls[3]["query"] == "SELECT count() FROM `ecommerce`.`orders`"


def test_seed_clickhouse_statement_splitter():
    assert seed_clickhouse._split_sql_statements("SELECT 1;  SELECT 2;\n") == ["SELECT 1", "SELECT 2"]


def test_clickhouse_schema_matches_duckdb_dataset_columns():
    sql = (Path(__file__).resolve().parents[2] / "docker" / "clickhouse" / "init.sql").read_text(
        encoding="utf-8"
    )

    assert "created_at" not in sql
    assert "name         String" in sql
    assert "order_id        String" in sql
    assert "item_id     String" in sql


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        duckdb_path=tmp_path / "ecommerce.duckdb",
        sqlite_path=tmp_path / "metadata.sqlite",
        clickhouse_host="localhost",
        clickhouse_port=8123,
        clickhouse_user="default",
        clickhouse_password="",
        clickhouse_database="ecommerce",
        clickhouse_max_execution_time=7,
    )
