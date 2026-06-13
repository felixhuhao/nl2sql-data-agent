from backend.app.dataspace.verified_queries import list_verified_queries
from backend.app.config import DEFAULT_BROWSE_LIMIT
from backend.app.core.llm_provider import MockLLMProvider, SQLGenerationRequest


def test_mock_provider_returns_verified_demo_sql():
    provider = _provider()

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
    provider = _provider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="查询 最近30天 每日 销售额 和 订单数",
            schema_context="# Schema Context",
        )
    )

    assert result.matched_query_id == "recent_30d_daily_sales"


def test_mock_provider_returns_region_sales_verified_sql():
    provider = _provider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="按地区统计最近30天销售额",
            schema_context="# Schema Context",
        )
    )

    assert result.matched_query_id == "recent_30d_region_sales"
    assert "JOIN dim_regions r ON o.region_key = r.region_key" in result.sql
    assert "r.region_group" in result.sql
    assert "SUM(o.payment_amount) AS sales_amount" in result.sql


def test_mock_provider_returns_channel_sales_verified_sql():
    provider = _provider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="按渠道统计最近30天销售额",
            schema_context="# Schema Context",
        )
    )

    assert result.matched_query_id == "recent_30d_channel_sales"
    assert "JOIN dim_channels c ON o.channel_key = c.channel_key" in result.sql
    assert "c.channel_name" in result.sql
    assert "SUM(o.payment_amount) AS sales_amount" in result.sql


def test_mock_provider_returns_top_products_verified_sql():
    provider = _provider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="最近30天销量最高的10个商品",
            schema_context="# Schema Context",
        )
    )

    assert result.matched_query_id == "recent_30d_top_products"
    assert "JOIN dim_products p ON i.product_key = p.product_key" in result.sql
    assert "SUM(i.quantity) AS quantity_sold" in result.sql
    assert "LIMIT 10" in result.sql


def test_mock_provider_accepts_olap_context_for_topn_question():
    provider = _provider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="最近30天销量最高的10个商品",
            schema_context="# Schema Context",
            olap_intents=["topn"],
            olap_hint="TopN / ranking SQL guidance",
        )
    )

    assert result.matched_query_id == "recent_30d_top_products"
    assert "LIMIT 10" in result.sql


def test_mock_provider_does_not_route_verified_queries_by_keyword_bag():
    provider = _provider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="最近30天销售额订单数地区渠道商品销量",
            schema_context="# Schema Context",
        )
    )

    assert result.matched_query_id is None
    assert (
        result.sql
        == f"SELECT order_id, payment_amount FROM fact_orders ORDER BY order_id LIMIT {DEFAULT_BROWSE_LIMIT}"
    )


def test_mock_provider_does_not_generate_write_sql_from_keywords():
    provider = _provider()

    for question in ("删除2024年数据", "DROP fact_orders", "创建一张临时订单表"):
        result = provider.generate_sql(
            SQLGenerationRequest(
                question=question,
                schema_context="# Schema Context",
            )
        )

        assert result.provider == "mock"
        assert result.matched_query_id is None
        assert (
            result.sql
            == f"SELECT order_id, payment_amount FROM fact_orders ORDER BY order_id LIMIT {DEFAULT_BROWSE_LIMIT}"
        )


def test_mock_provider_returns_fallback_select():
    provider = _provider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="随便查几条订单",
            schema_context="# Schema Context",
        )
    )

    assert result.provider == "mock"
    assert result.matched_query_id is None
    assert (
        result.sql
        == f"SELECT order_id, payment_amount FROM fact_orders ORDER BY order_id LIMIT {DEFAULT_BROWSE_LIMIT}"
    )


def test_mock_provider_uses_configured_browse_limit_for_fallback_select():
    provider = _provider()

    result = provider.generate_sql(
        SQLGenerationRequest(
            question="随便查几条订单",
            schema_context="# Schema Context",
            default_browse_limit=25,
        )
    )

    assert result.provider == "mock"
    assert result.matched_query_id is None
    assert result.sql == "SELECT order_id, payment_amount FROM fact_orders ORDER BY order_id LIMIT 25"


def _provider() -> MockLLMProvider:
    return MockLLMProvider(verified_queries_provider=_verified_query_payloads)


def _verified_query_payloads(datasource_name: str = "duckdb_ecommerce") -> list[dict]:
    del datasource_name
    return [
        {
            "id": query.id,
            "question": query.question,
            "sql": query.sql,
            "tags": list(query.tags),
            "verified_by": query.verified_by,
        }
        for query in list_verified_queries()
    ]
