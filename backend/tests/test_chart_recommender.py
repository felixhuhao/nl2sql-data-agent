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


def test_recommend_chart_returns_line_for_week_start_alias():
    recommendation = recommend_chart(
        QueryResult(
            columns=["week_start", "active_users"],
            rows=[["2025-12-29", 12]],
            row_count=1,
        )
    )

    assert recommendation.chart_type == "line"
    assert recommendation.x_column == "week_start"
    assert recommendation.y_columns == ["active_users"]


def test_recommend_chart_returns_line_for_date_alias():
    recommendation = recommend_chart(
        QueryResult(
            columns=["order_date", "sales_amount"],
            rows=[["2025-12-31", 100]],
            row_count=1,
        )
    )

    assert recommendation.chart_type == "line"
    assert recommendation.x_column == "order_date"
    assert recommendation.y_columns == ["sales_amount"]


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


def test_recommend_chart_returns_bar_for_category_metric_rows():
    recommendation = recommend_chart(
        QueryResult(
            columns=["channel_name", "sales_amount"],
            rows=[["官网", 100], ["天猫", 90]],
            row_count=2,
        )
    )

    assert recommendation.chart_type == "bar"
    assert recommendation.x_column == "channel_name"
    assert recommendation.y_columns == ["sales_amount"]


def test_recommend_chart_returns_table_for_single_category_metric_row():
    recommendation = recommend_chart(
        QueryResult(
            columns=["region_group", "order_count"],
            rows=[["华东", 1932]],
            row_count=1,
        )
    )

    assert recommendation.chart_type == "table"
    assert recommendation.reason == "Single category row is clearer as a table."


def test_recommend_chart_prefers_bar_for_topn_intent():
    recommendation = recommend_chart(
        QueryResult(
            columns=["product_name", "sales_amount"],
            rows=[["商品A", 100], ["商品B", 90]],
            row_count=2,
        ),
        olap_intents=["topn"],
    )

    assert recommendation.chart_type == "bar"
    assert recommendation.x_column == "product_name"
    assert recommendation.y_columns == ["sales_amount"]
    assert recommendation.reason == "TopN analysis detected."


def test_recommend_chart_returns_dual_axis_for_yoy_comparison():
    recommendation = recommend_chart(
        QueryResult(
            columns=["month", "sales_amount", "prev_year_sales", "yoy_pct"],
            rows=[["2025-12", 100000, 80000, 25.0]],
            row_count=1,
        ),
        olap_intents=["yoy_mom"],
    )

    assert recommendation.chart_type == "dual_axis"
    assert recommendation.x_column == "month"
    assert recommendation.y_columns == ["sales_amount", "yoy_pct"]


def test_recommend_chart_uses_period_axis_for_yoy_comparison():
    recommendation = recommend_chart(
        QueryResult(
            columns=["period", "sales_amount", "prev_year_sales", "yoy_pct"],
            rows=[["2025-12-01", 100000, 80000, 25.0]],
            row_count=1,
        ),
        olap_intents=["yoy_mom"],
    )

    assert recommendation.chart_type == "dual_axis"
    assert recommendation.x_column == "period"
    assert recommendation.y_columns == ["sales_amount", "yoy_pct"]


def test_recommend_chart_keeps_dual_axis_when_yoy_pct_is_all_null():
    recommendation = recommend_chart(
        QueryResult(
            columns=["month", "sales_amount", "prev_year_sales", "yoy_pct"],
            rows=[
                ["2025-01", 100000, None, None],
                ["2025-02", 120000, None, None],
            ],
            row_count=2,
        ),
        olap_intents=["yoy_mom"],
    )

    assert recommendation.chart_type == "dual_axis"
    assert recommendation.x_column == "month"
    assert recommendation.y_columns == ["sales_amount", "yoy_pct"]


def test_recommend_chart_keeps_same_scale_yoy_metrics_on_line():
    recommendation = recommend_chart(
        QueryResult(
            columns=["month", "sales_amount", "prev_year_sales"],
            rows=[["2025-12", 100, 80]],
            row_count=1,
        ),
        olap_intents=["yoy_mom"],
    )

    assert recommendation.chart_type == "line"
    assert recommendation.x_column == "month"
    assert recommendation.y_columns == ["sales_amount", "prev_year_sales"]


def test_recommend_chart_avoids_dual_axis_without_base_metric():
    recommendation = recommend_chart(
        QueryResult(
            columns=["month", "yoy_pct", "mom_pct"],
            rows=[["2025-12", 25.0, 4.2]],
            row_count=1,
        ),
        olap_intents=["yoy_mom"],
    )

    assert recommendation.chart_type == "line"
    assert recommendation.x_column == "month"
    assert recommendation.y_columns == ["yoy_pct", "mom_pct"]


def test_recommend_chart_uses_token_matching_for_comparison_columns():
    recommendation = recommend_chart(
        QueryResult(
            columns=["month", "sales_amount", "priority_rate", "momentum_sales", "exchange_rate"],
            rows=[["2025-12", 100, 80, 90, 70]],
            row_count=1,
        ),
        olap_intents=["yoy_mom"],
    )

    assert recommendation.chart_type == "line"
    assert recommendation.x_column == "month"
    assert recommendation.y_columns == ["sales_amount", "priority_rate", "momentum_sales", "exchange_rate"]


def test_recommend_chart_returns_line_for_moving_average_intent():
    recommendation = recommend_chart(
        QueryResult(
            columns=["date_value", "sales_amount", "ma_7d"],
            rows=[["2025-12-31", 100, 95]],
            row_count=1,
        ),
        olap_intents=["moving_avg"],
    )

    assert recommendation.chart_type == "line"
    assert recommendation.reason == "Moving average analysis detected."
    assert recommendation.y_columns == ["sales_amount", "ma_7d"]


def test_recommend_chart_returns_pie_for_distribution_share():
    recommendation = recommend_chart(
        QueryResult(
            columns=["channel_name", "channel_share"],
            rows=[["官网", 0.4], ["天猫", 0.35], ["京东", 0.25]],
            row_count=3,
        )
    )

    assert recommendation.chart_type == "pie"
    assert recommendation.x_column == "channel_name"
    assert recommendation.y_columns == ["channel_share"]


def test_recommend_chart_prefers_percentage_column_for_distribution_pie():
    recommendation = recommend_chart(
        QueryResult(
            columns=["channel_name", "sales_amount", "sales_percentage"],
            rows=[["官网", 100, 40.0], ["天猫", 90, 35.0], ["京东", 80, 25.0]],
            row_count=3,
        )
    )

    assert recommendation.chart_type == "pie"
    assert recommendation.x_column == "channel_name"
    assert recommendation.y_columns == ["sales_percentage"]


def test_recommend_chart_does_not_treat_yoy_pct_as_distribution_pie():
    recommendation = recommend_chart(
        QueryResult(
            columns=["channel_name", "sales_amount", "yoy_pct"],
            rows=[["官网", 100, 0.4], ["天猫", 90, 0.35]],
            row_count=2,
        )
    )

    assert recommendation.chart_type == "bar"
    assert recommendation.x_column == "channel_name"
    assert recommendation.y_columns == ["sales_amount"]


def test_recommend_chart_returns_table_for_too_many_category_rows():
    recommendation = recommend_chart(
        QueryResult(
            columns=["channel_name", "sales_amount"],
            rows=[[f"渠道{i}", i * 100] for i in range(31)],
            row_count=31,
        )
    )

    assert recommendation.chart_type == "table"


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
