from backend.app.execution.runner import QueryResult
from backend.app.visualization.recommender import recommend_chart


def test_recommend_chart_returns_line_for_time_series_metrics():
    recommendation = recommend_chart(
        QueryResult(
            columns=["date_value", "sales_amount", "order_count"],
            rows=[["2025-12-31", 100, 2]],
            row_count=1,
        )
    )

    assert recommendation.chart_type == "line"
    assert recommendation.x_column == "date_value"
    assert recommendation.y_columns == ["sales_amount"]


def test_recommend_chart_keeps_same_scale_time_series_metrics():
    recommendation = recommend_chart(
        QueryResult(
            columns=["date_value", "total_amount", "payment_amount"],
            rows=[["2025-12-31", 100, 90]],
            row_count=1,
        )
    )

    assert recommendation.chart_type == "line"
    assert recommendation.x_column == "date_value"
    assert recommendation.y_columns == ["total_amount", "payment_amount"]


def test_recommend_chart_returns_table_for_detail_rows():
    recommendation = recommend_chart(
        QueryResult(
            columns=[
                "order_id",
                "user_key",
                "region_key",
                "channel_key",
                "date_key",
                "total_amount",
                "discount_amount",
                "payment_amount",
                "order_status",
            ],
            rows=[["O00000001", 1, 2, 3, 20240509, 100, 0, 100, "paid"] for _ in range(30)],
            row_count=30,
        )
    )

    assert recommendation.chart_type == "table"
    assert recommendation.reason == "Result looks like record-level detail rows."


def test_recommend_chart_returns_table_for_short_order_detail_rows():
    recommendation = recommend_chart(
        QueryResult(
            columns=[
                "order_id",
                "user_key",
                "region_key",
                "channel_key",
                "date_key",
                "total_amount",
                "discount_amount",
                "payment_amount",
                "order_status",
            ],
            rows=[
                ["O00000001", 1, 2, 3, 20240509, 100, 0, 100, "paid"],
                ["O00000002", 1, 2, 3, 20240510, 120, 0, 120, "paid"],
            ],
            row_count=2,
        )
    )

    assert recommendation.chart_type == "table"
    assert recommendation.reason == "Result looks like record-level detail rows."


def test_recommend_chart_ignores_date_key_as_time_axis():
    recommendation = recommend_chart(
        QueryResult(
            columns=["date_key", "sales_amount"],
            rows=[[20251231, 100]],
            row_count=1,
        )
    )

    assert recommendation.chart_type == "table"


def test_recommend_chart_returns_table_when_no_time_column():
    recommendation = recommend_chart(
        QueryResult(
            columns=["channel_name", "sales_amount"],
            rows=[["官网", 100]],
            row_count=1,
        )
    )

    assert recommendation.chart_type == "table"
    assert recommendation.x_column is None
    assert recommendation.y_columns == []


def test_recommend_chart_returns_table_when_no_metric_column():
    recommendation = recommend_chart(
        QueryResult(
            columns=["date_value", "channel_name"],
            rows=[["2025-12-31", "官网"]],
            row_count=1,
        )
    )

    assert recommendation.chart_type == "table"


def test_recommend_chart_returns_table_for_empty_result_shape():
    recommendation = recommend_chart(QueryResult(columns=[], rows=[], row_count=0))

    assert recommendation.chart_type == "table"
    assert recommendation.reason == "No columns returned."
