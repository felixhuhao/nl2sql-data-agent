from backend.app.agent.sql_postprocess import normalize_generated_sql


def test_normalize_generated_sql_aliases_sales_amount_metric():
    sql = "SELECT SUM(fact_orders.payment_amount) FROM fact_orders"

    assert normalize_generated_sql(sql) == "SELECT SUM(fact_orders.payment_amount) AS sales_amount FROM fact_orders"


def test_normalize_generated_sql_aliases_sales_amount_with_table_alias():
    sql = "SELECT SUM(o.payment_amount) FROM fact_orders AS o"

    assert normalize_generated_sql(sql) == "SELECT SUM(o.payment_amount) AS sales_amount FROM fact_orders AS o"


def test_normalize_generated_sql_aliases_order_count_and_aov():
    order_count_sql = "SELECT COUNT(DISTINCT o.order_id) FROM fact_orders o"
    aov_sql = "SELECT SUM(o.payment_amount) / COUNT(DISTINCT o.order_id) FROM fact_orders o"

    assert normalize_generated_sql(order_count_sql) == (
        "SELECT COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders AS o"
    )
    assert normalize_generated_sql(aov_sql) == (
        "SELECT SUM(o.payment_amount) / COUNT(DISTINCT o.order_id) AS aov FROM fact_orders AS o"
    )


def test_normalize_generated_sql_preserves_existing_alias():
    sql = "SELECT SUM(fact_orders.payment_amount) AS total_revenue FROM fact_orders"

    assert normalize_generated_sql(sql) == sql


def test_normalize_generated_sql_returns_unparseable_sql_unchanged():
    sql = "SELECT FROM"

    assert normalize_generated_sql(sql) == sql
