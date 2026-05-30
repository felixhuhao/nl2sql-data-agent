from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedQuery:
    id: str
    question: str
    sql: str
    tags: tuple[str, ...]
    verified_by: str = "system"


VERIFIED_QUERIES = (
    VerifiedQuery(
        id="recent_30d_daily_sales",
        question="查询最近30天每日销售额和订单数",
        sql=(
            "SELECT d.date_value, "
            "SUM(o.payment_amount) AS sales_amount, "
            "COUNT(DISTINCT o.order_id) AS order_count "
            "FROM fact_orders o "
            "JOIN dim_date d ON o.date_key = d.date_key "
            "WHERE d.date_value BETWEEN DATE '2025-12-02' AND DATE '2025-12-31' "
            "GROUP BY d.date_value "
            "ORDER BY d.date_value"
        ),
        tags=("sales", "time_series", "demo"),
    ),
    VerifiedQuery(
        id="recent_30d_region_sales",
        question="按地区统计最近30天销售额",
        sql=(
            "SELECT r.region_group, "
            "SUM(o.payment_amount) AS sales_amount "
            "FROM fact_orders o "
            "JOIN dim_regions r ON o.region_key = r.region_key "
            "JOIN dim_date d ON o.date_key = d.date_key "
            "WHERE d.date_value BETWEEN DATE '2025-12-02' AND DATE '2025-12-31' "
            "GROUP BY r.region_group "
            "ORDER BY sales_amount DESC"
        ),
        tags=("sales", "region", "demo"),
    ),
    VerifiedQuery(
        id="recent_30d_channel_sales",
        question="按渠道统计最近30天销售额",
        sql=(
            "SELECT c.channel_name, "
            "SUM(o.payment_amount) AS sales_amount "
            "FROM fact_orders o "
            "JOIN dim_channels c ON o.channel_key = c.channel_key "
            "JOIN dim_date d ON o.date_key = d.date_key "
            "WHERE d.date_value BETWEEN DATE '2025-12-02' AND DATE '2025-12-31' "
            "GROUP BY c.channel_name "
            "ORDER BY sales_amount DESC"
        ),
        tags=("sales", "channel", "demo"),
    ),
    VerifiedQuery(
        id="recent_30d_top_products",
        question="最近30天销量最高的10个商品",
        sql=(
            "SELECT p.name AS product_name, "
            "SUM(i.quantity) AS quantity_sold "
            "FROM fact_order_items i "
            "JOIN dim_products p ON i.product_key = p.product_key "
            "JOIN dim_date d ON i.date_key = d.date_key "
            "WHERE d.date_value BETWEEN DATE '2025-12-02' AND DATE '2025-12-31' "
            "GROUP BY p.name "
            "ORDER BY quantity_sold DESC "
            "LIMIT 10"
        ),
        tags=("product", "topn", "demo"),
    ),
)


def list_verified_queries() -> tuple[VerifiedQuery, ...]:
    return VERIFIED_QUERIES
