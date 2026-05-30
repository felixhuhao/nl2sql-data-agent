from backend.app.core.llm_provider import MockLLMProvider, SQLGenerationRequest


def test_mock_provider_returns_verified_demo_sql():
    provider = MockLLMProvider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="查询最近30天每日销售额和订单数",
            schema_context="# Schema Context",
        )
    )

    assert result.provider == "mock"
    assert result.matched_query_id == "recent_30d_daily_sales"
    assert "SUM(o.payment_amount) AS sales_amount" in result.sql
    assert "COUNT(DISTINCT o.order_id) AS order_count" in result.sql


def test_mock_provider_matches_demo_question_with_spaces():
    provider = MockLLMProvider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="查询 最近30天 每日 销售额 和 订单数",
            schema_context="# Schema Context",
        )
    )

    assert result.matched_query_id == "recent_30d_daily_sales"


def test_mock_provider_returns_delete_sql_for_delete_question():
    provider = MockLLMProvider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="删除2024年数据",
            schema_context="# Schema Context",
        )
    )

    assert result.sql.startswith("DELETE FROM fact_orders")


def test_mock_provider_returns_drop_sql_for_drop_question():
    provider = MockLLMProvider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="DROP fact_orders",
            schema_context="# Schema Context",
        )
    )

    assert result.sql == "DROP TABLE fact_orders"


def test_mock_provider_returns_create_sql_for_create_question():
    provider = MockLLMProvider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="创建一张临时订单表",
            schema_context="# Schema Context",
        )
    )

    assert result.sql.startswith("CREATE TABLE tmp_orders")


def test_mock_provider_returns_fallback_select():
    provider = MockLLMProvider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="随便查几条订单",
            schema_context="# Schema Context",
        )
    )

    assert result.provider == "mock"
    assert result.matched_query_id is None
    assert result.sql == "SELECT order_id, payment_amount FROM fact_orders ORDER BY order_id LIMIT 20"
