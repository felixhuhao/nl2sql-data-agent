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
)


def list_verified_queries() -> tuple[VerifiedQuery, ...]:
    return VERIFIED_QUERIES
