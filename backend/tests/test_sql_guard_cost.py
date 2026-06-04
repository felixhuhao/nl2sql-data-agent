from backend.app.sql_guard import GuardScope, guard_sql


def test_limit_is_added_when_missing():
    result = guard_sql("SELECT order_id FROM fact_orders", scope=_scope())

    assert result.allowed is True
    assert result.normalized_sql == "SELECT order_id FROM fact_orders LIMIT 500"
    assert result.warnings == ["LIMIT 500 was added automatically."]


def test_limit_is_not_added_for_scalar_aggregate():
    result = guard_sql("SELECT SUM(payment_amount) AS sales_amount FROM fact_orders", scope=_scope())

    assert result.allowed is True
    assert result.normalized_sql == "SELECT SUM(payment_amount) AS sales_amount FROM fact_orders"
    assert result.warnings == []


def test_existing_small_limit_is_preserved():
    result = guard_sql("SELECT order_id FROM fact_orders LIMIT 100", scope=_scope())

    assert result.allowed is True
    assert result.normalized_sql == "SELECT order_id FROM fact_orders LIMIT 100"
    assert result.warnings == []


def test_existing_large_limit_is_capped():
    result = guard_sql("SELECT order_id FROM fact_orders LIMIT 1000", scope=_scope())

    assert result.allowed is True
    assert result.normalized_sql == "SELECT order_id FROM fact_orders LIMIT 500"
    assert result.warnings == ["LIMIT 1000 was capped to 500."]


def test_non_literal_limit_is_rejected():
    result = guard_sql("SELECT order_id FROM fact_orders LIMIT order_id", scope=_scope())

    assert result.allowed is False
    assert result.stage == "cost_guard"
    assert result.reason == "LIMIT must be an integer literal."


def test_negative_limit_is_rejected():
    result = guard_sql("SELECT order_id FROM fact_orders LIMIT -1", scope=_scope())

    assert result.allowed is False
    assert result.stage == "cost_guard"
    assert result.reason == "LIMIT must be non-negative."


def _scope() -> GuardScope:
    return GuardScope(
        allowed_tables=frozenset({"fact_orders"}),
        table_columns={
            "fact_orders": frozenset({"order_id", "payment_amount"}),
        },
    )
