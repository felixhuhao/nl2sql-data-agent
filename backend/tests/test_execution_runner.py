import pytest

from backend.app.execution import runner
from backend.app.connectors.schema import RawResult
from backend.app.sql_guard.models import GuardResult


def test_execute_guarded_sql_delegates_to_datasource_connector(monkeypatch):
    calls = []

    class FakeConnector:
        def execute(self, sql: str):
            calls.append(sql)
            return RawResult(columns=["id", "amount"], rows=[[1, 10], [2, 20]], row_count=2)

    class FakeManager:
        def get(self, datasource_name: str):
            assert datasource_name == "clickhouse_ecommerce"
            return FakeConnector()

    monkeypatch.setattr(runner, "get_datasource_manager", lambda: FakeManager())

    result = runner.execute_guarded_sql(
        GuardResult(
            allowed=True,
            stage="passed",
            normalized_sql="SELECT id, amount FROM orders ORDER BY id LIMIT 2",
        ),
        datasource_name="clickhouse_ecommerce",
    )

    assert calls == ["SELECT id, amount FROM orders ORDER BY id LIMIT 2"]
    assert result.columns == ["id", "amount"]
    assert result.rows == [[1, 10], [2, 20]]
    assert result.row_count == 2
    assert result.elapsed_ms is not None


def test_execute_guarded_sql_uses_clickhouse_execute_with_explain(monkeypatch):
    calls = []

    class FakeConnector:
        dialect = "clickhouse"

        def execute_with_explain(self, sql: str):
            calls.append(sql)
            return (
                RawResult(columns=["id"], rows=[[1]], row_count=1),
                {"lines": ["HashJoin"]},
            )

    class FakeManager:
        def get(self, datasource_name: str):
            assert datasource_name == "clickhouse_ecommerce"
            return FakeConnector()

    monkeypatch.setattr(runner, "get_datasource_manager", lambda: FakeManager())

    result = runner.execute_guarded_sql(
        GuardResult(
            allowed=True,
            stage="passed",
            normalized_sql="SELECT id FROM orders LIMIT 2",
        ),
        datasource_name="clickhouse_ecommerce",
    )

    assert calls == ["SELECT id FROM orders LIMIT 2"]
    assert result.columns == ["id"]
    assert result.explain_plan == {"lines": ["HashJoin"]}


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


def test_execute_guarded_sql_propagates_connector_errors(monkeypatch):
    class FakeConnector:
        def execute(self, sql: str):
            raise RuntimeError(f"readonly connector rejected: {sql}")

    class FakeManager:
        def get(self, datasource_name: str):
            return FakeConnector()

    monkeypatch.setattr(runner, "get_datasource_manager", lambda: FakeManager())

    with pytest.raises(RuntimeError, match="readonly connector rejected"):
        runner.execute_guarded_sql(
            GuardResult(
                allowed=True,
                stage="passed",
                normalized_sql="CREATE TABLE should_fail AS SELECT 1 AS id",
            )
        )
