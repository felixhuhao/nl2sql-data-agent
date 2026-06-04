import pytest

from backend.app.sql_guard import guard_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT order_id FROM fact_orders",
        "select order_id, payment_amount from fact_orders where payment_amount > 100",
    ],
)
def test_select_is_allowed(sql):
    result = guard_sql(sql)

    assert result.allowed is True
    assert result.stage == "passed"
    assert result.normalized_sql is not None


@pytest.mark.parametrize(
    "sql",
    [
        "/* comment */ SELECT 1",
        "-- comment\nSELECT 1",
    ],
)
def test_select_with_leading_comment_is_allowed(sql):
    result = guard_sql(sql)

    assert result.allowed is True
    assert result.stage == "passed"


def test_multi_statement_is_rejected():
    result = guard_sql("SELECT 1; SELECT 2")

    assert result.allowed is False
    assert result.stage == "syntax_guard"


def test_empty_sql_is_rejected():
    result = guard_sql("  ")

    assert result.allowed is False
    assert result.stage == "syntax_guard"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO fact_orders VALUES (1)",
        "UPDATE fact_orders SET payment_amount = 0",
        "DELETE FROM fact_orders",
        "DROP TABLE fact_orders",
        "ALTER TABLE fact_orders ADD COLUMN tmp INTEGER",
        "CREATE TABLE tmp_orders AS SELECT * FROM fact_orders",
        "TRUNCATE TABLE fact_orders",
        "COPY fact_orders TO 'orders.csv'",
        "INSTALL httpfs",
        "LOAD httpfs",
    ],
)
def test_blocked_operations_are_rejected(sql):
    result = guard_sql(sql)

    assert result.allowed is False
    assert result.stage in {"syntax_guard", "operation_guard"}


def test_blocked_operation_after_leading_comment_is_rejected_by_command():
    result = guard_sql("/* comment */ DROP TABLE fact_orders")

    assert result.allowed is False
    assert result.stage == "operation_guard"
    assert result.reason == "DROP is not allowed."


def test_union_has_specific_rejection_reason():
    result = guard_sql("SELECT 1 UNION SELECT 2")

    assert result.allowed is False
    assert result.stage == "operation_guard"
    assert result.reason == "UNION is not allowed in Phase 1."


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('orders.csv')",
        "SELECT * FROM read_json('orders.json')",
        "SELECT * FROM read_parquet('orders.parquet')",
    ],
)
def test_external_read_functions_are_rejected(sql):
    result = guard_sql(sql)

    assert result.allowed is False
    assert result.stage == "function_guard"


def test_clickhouse_select_uses_clickhouse_dialect():
    result = guard_sql(
        "SELECT toStartOfMonth(date_value) AS month FROM dim_date",
        datasource_name="clickhouse_ecommerce",
    )

    assert result.allowed is True
    assert result.stage == "passed"
    assert result.normalized_sql is not None
    assert "dateTrunc('MONTH', date_value)" in result.normalized_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SYSTEM FLUSH LOGS",
        "KILL QUERY WHERE query_id = 'x'",
        "RENAME TABLE old_name TO new_name",
        "EXCHANGE TABLES a AND b",
    ],
)
def test_clickhouse_blocked_operations_are_rejected(sql):
    result = guard_sql(sql, datasource_name="clickhouse_ecommerce")

    assert result.allowed is False
    assert result.stage == "operation_guard"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM s3('https://example.com/file.csv')",
        "SELECT * FROM url('https://example.com/file.csv')",
        "SELECT * FROM remote('host', db, table)",
    ],
)
def test_clickhouse_external_functions_are_rejected(sql):
    result = guard_sql(sql, datasource_name="clickhouse_ecommerce")

    assert result.allowed is False
    assert result.stage == "function_guard"


def test_clickhouse_insert_into_function_is_rejected_as_function_guard():
    result = guard_sql(
        "INSERT INTO FUNCTION url('https://example.com/out.csv') SELECT * FROM fact_orders",
        datasource_name="clickhouse_ecommerce",
    )

    assert result.allowed is False
    assert result.stage == "function_guard"
