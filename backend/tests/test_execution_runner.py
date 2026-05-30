from pathlib import Path
from uuid import uuid4

import duckdb
import pytest

from backend.app.execution import runner
from backend.app.sql_guard.models import GuardResult


def test_execute_guarded_sql_uses_readonly_connection(monkeypatch):
    db_path = _create_test_duckdb()
    try:
        read_only_flags = []

        def fake_get_duckdb_connection(read_only=False):
            read_only_flags.append(read_only)
            return duckdb.connect(str(db_path), read_only=read_only)

        monkeypatch.setattr(runner, "get_duckdb_connection", fake_get_duckdb_connection)

        result = runner.execute_guarded_sql(
            GuardResult(
                allowed=True,
                stage="passed",
                normalized_sql="SELECT id, amount FROM orders ORDER BY id LIMIT 2",
            )
        )

        assert read_only_flags == [True]
        assert result.columns == ["id", "amount"]
        assert result.rows == [[1, 10], [2, 20]]
        assert result.row_count == 2
    finally:
        _delete_test_duckdb(db_path)


def test_execute_guarded_sql_rejects_failed_guard_result():
    guard_result = GuardResult(
        allowed=False,
        stage="operation_guard",
        reason="DROP is not allowed.",
    )

    with pytest.raises(ValueError, match="SQL was rejected by operation_guard"):
        runner.execute_guarded_sql(guard_result)


def test_execute_guarded_sql_requires_normalized_sql():
    guard_result = GuardResult(allowed=True, stage="passed")

    with pytest.raises(ValueError, match="normalized_sql"):
        runner.execute_guarded_sql(guard_result)


def test_readonly_connection_rejects_write_sql(monkeypatch):
    db_path = _create_test_duckdb()
    try:
        def fake_get_duckdb_connection(read_only=False):
            return duckdb.connect(str(db_path), read_only=read_only)

        monkeypatch.setattr(runner, "get_duckdb_connection", fake_get_duckdb_connection)

        with pytest.raises(duckdb.Error):
            runner.execute_guarded_sql(
                GuardResult(
                    allowed=True,
                    stage="passed",
                    normalized_sql="CREATE TABLE should_fail AS SELECT 1 AS id",
                )
            )
    finally:
        _delete_test_duckdb(db_path)


def _create_test_duckdb() -> Path:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    db_path = data_dir / f"test_execution_runner_{uuid4().hex}.duckdb"
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("CREATE TABLE orders (id INTEGER, amount INTEGER)")
        connection.execute("INSERT INTO orders VALUES (1, 10), (2, 20), (3, 30)")
    finally:
        connection.close()
    return db_path


def _delete_test_duckdb(db_path: Path) -> None:
    for path in [db_path, db_path.with_suffix(".duckdb.wal")]:
        if path.exists():
            path.unlink()
