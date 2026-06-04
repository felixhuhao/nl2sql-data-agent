from backend.app.agent.performance import parse_plan_hints


def test_parse_plan_hints_reads_parts_sorting_join_and_missing_time_filter():
    hints = parse_plan_hints(
        {
            "lines": [
                "ReadFromMergeTree Parts: 2/12 SortingKey",
                "HashJoin",
            ]
        },
        matched_tables=["fact_orders"],
        sql="SELECT * FROM fact_orders",
    )

    assert hints == [
        "命中分区裁剪，扫描 2/12 parts。",
        "查询计划包含排序键相关读取。",
        "包含 1 个 JOIN。",
        "建议添加明确的时间范围过滤以减少 ClickHouse 扫描。",
    ]


def test_parse_plan_hints_does_not_suggest_time_filter_when_present():
    hints = parse_plan_hints(
        {"text": "ReadFromMergeTree Parts: 12/12"},
        matched_tables=["fact_orders"],
        sql="SELECT * FROM fact_orders WHERE date_key >= 20251201",
    )

    assert hints == ["未明显命中分区裁剪，扫描 12/12 parts。"]


def test_parse_plan_hints_handles_empty_parts_without_misleading_pruning_hint():
    hints = parse_plan_hints(
        {"text": "ReadFromMergeTree Parts: 0/0"},
        matched_tables=["fact_orders"],
        sql="SELECT * FROM fact_orders",
    )

    assert hints == [
        "查询计划显示没有可扫描的 parts。",
        "建议添加明确的时间范围过滤以减少 ClickHouse 扫描。",
    ]


def test_parse_plan_hints_detects_time_filter_inside_subquery():
    hints = parse_plan_hints(
        {"text": "ReadFromMergeTree Parts: 12/12"},
        matched_tables=["fact_orders"],
        sql=(
            "SELECT month, SUM(metric_value) FROM ("
            "SELECT toStartOfMonth(date_value) AS month, payment_amount AS metric_value "
            "FROM fact_orders WHERE date_key >= 20251201 GROUP BY month, payment_amount"
            ") base GROUP BY month"
        ),
    )

    assert hints == ["未明显命中分区裁剪，扫描 12/12 parts。"]


def test_parse_plan_hints_returns_default_success_hint():
    assert parse_plan_hints({"text": "ExpressionTransform"}, sql="SELECT 1") == [
        "EXPLAIN 已生成，未发现明显性能风险。"
    ]
