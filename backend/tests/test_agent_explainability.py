from backend.app.agent.explainability import build_query_explainability
from backend.app.sql_guard.models import GuardResult


def test_build_query_explainability_extracts_assets_and_date_rule():
    result = build_query_explainability(
        sql=(
            "SELECT d.date_value, SUM(o.payment_amount) AS sales_amount "
            "FROM fact_orders AS o "
            "JOIN dim_date AS d ON o.date_key = d.date_key "
            "WHERE d.date_value BETWEEN DATE '2025-12-02' AND DATE '2025-12-31' "
            "GROUP BY d.date_value LIMIT 500"
        ),
        question="查询最近30天每日销售额和订单数",
        guard_result=GuardResult(
            allowed=True,
            stage="passed",
            normalized_sql="SELECT 1 LIMIT 500",
        ),
    )

    assert result["matched_tables"] == ["dim_date", "fact_orders"]
    assert "dim_date.date_value" in result["matched_columns"]
    assert "fact_orders.payment_amount" in result["matched_columns"]
    assert result["date_interpretation"] == {
        "matched": True,
        "phrase": "最近30天",
        "dataset_current_date": "2025-12-31",
        "start": "2025-12-02",
        "end": "2025-12-31",
    }
    assert result["guard_result"]["allowed"] is True


def test_build_query_explainability_handles_rejected_sql():
    result = build_query_explainability(
        sql="DELETE FROM fact_orders",
        question="删除2024年数据",
        guard_result=GuardResult(
            allowed=False,
            stage="operation_guard",
            reason="DELETE is not allowed.",
        ),
    )

    assert result["matched_tables"] == ["fact_orders"]
    assert result["matched_columns"] == []
    assert result["date_interpretation"] == {
        "matched": False,
        "dataset_current_date": "2025-12-31",
    }
    assert result["guard_result"]["allowed"] is False
    assert result["guard_result"]["reason"] == "DELETE is not allowed."
