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
    assert recommendation.y_columns == ["sales_amount", "order_count"]


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
