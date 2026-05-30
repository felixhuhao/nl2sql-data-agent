from backend.app.sql_guard import GuardScope, guard_sql


def test_allowed_table_and_unique_column_pass():
    result = guard_sql("SELECT payment_amount FROM fact_orders", scope=_scope())

    assert result.allowed is True
    assert result.stage == "passed"


def test_allowed_alias_columns_pass():
    result = guard_sql(
        """
        SELECT o.order_id, d.date_value
        FROM fact_orders o
        JOIN dim_date d ON o.order_date_key = d.date_key
        """,
        scope=_scope(),
    )

    assert result.allowed is True
    assert result.stage == "passed"


def test_star_on_allowed_table_passes():
    result = guard_sql("SELECT * FROM fact_orders", scope=_scope())

    assert result.allowed is True
    assert result.stage == "passed"


def test_order_by_projection_alias_passes():
    result = guard_sql(
        """
        SELECT SUM(payment_amount) AS sales_amount
        FROM fact_orders
        ORDER BY sales_amount
        """,
        scope=_scope(),
    )

    assert result.allowed is True
    assert result.stage == "passed"


def test_non_whitelist_table_is_rejected():
    result = guard_sql("SELECT order_id FROM raw_orders", scope=_scope())

    assert result.allowed is False
    assert result.stage == "scope_guard"
    assert result.reason == "Table raw_orders is not allowed."


def test_non_whitelist_column_is_rejected():
    result = guard_sql("SELECT secret_note FROM fact_orders", scope=_scope())

    assert result.allowed is False
    assert result.stage == "scope_guard"
    assert result.reason == "Column secret_note is not allowed."


def test_unknown_qualifier_is_rejected():
    result = guard_sql("SELECT x.order_id FROM fact_orders o", scope=_scope())

    assert result.allowed is False
    assert result.stage == "scope_guard"
    assert result.reason == "Unknown table qualifier x."


def test_qualified_non_whitelist_column_is_rejected():
    result = guard_sql("SELECT o.secret_note FROM fact_orders o", scope=_scope())

    assert result.allowed is False
    assert result.stage == "scope_guard"
    assert result.reason == "Column fact_orders.secret_note is not allowed."


def test_ambiguous_unqualified_column_is_rejected():
    result = guard_sql(
        """
        SELECT region_key
        FROM fact_orders
        JOIN dim_regions ON fact_orders.region_key = dim_regions.region_key
        """,
        scope=_scope(),
    )

    assert result.allowed is False
    assert result.stage == "scope_guard"
    assert result.reason == "Column region_key is ambiguous."


def _scope() -> GuardScope:
    return GuardScope(
        allowed_tables=frozenset({"fact_orders", "dim_date", "dim_regions"}),
        table_columns={
            "fact_orders": frozenset(
                {
                    "order_id",
                    "order_date_key",
                    "region_key",
                    "payment_amount",
                }
            ),
            "dim_date": frozenset({"date_key", "date_value"}),
            "dim_regions": frozenset({"region_key", "region_name"}),
        },
    )
