from backend.app.agent.olap_intent import build_olap_hint, detect_olap_intents, describe_olap_intents


def test_detect_olap_intents_returns_empty_for_basic_query():
    assert detect_olap_intents("查询最近30天每日销售额和订单数") == []


def test_detect_olap_intents_returns_priority_order_for_composite_question():
    assert detect_olap_intents("查询销售额前10的商品同比增长") == ["topn", "yoy_mom"]


def test_detect_olap_intents_supports_chinese_numerals_for_topn():
    assert detect_olap_intents("查询销售额前十的商品") == ["topn"]


def test_detect_olap_intents_supports_bare_and_hyphenated_english_topn():
    assert detect_olap_intents("show top products by sales") == ["topn"]
    assert detect_olap_intents("show top-10 products by sales") == ["topn"]
    assert detect_olap_intents("show top n products by sales") == ["topn"]


def test_detect_olap_intents_avoids_top_word_false_positives():
    assert detect_olap_intents("top-line revenue overview") == []
    assert detect_olap_intents("top-down revenue overview") == []
    assert detect_olap_intents("top-level revenue overview") == []
    assert detect_olap_intents("topdown analysis") == []
    assert detect_olap_intents("topnotch channel performance") == []
    assert detect_olap_intents("topnumber channel performance") == []


def test_detect_olap_intents_detects_moving_average():
    assert detect_olap_intents("查询最近30天销售额7日移动平均") == ["moving_avg"]


def test_build_olap_hint_includes_metric_context_and_intent_guidance():
    hint = build_olap_hint(
        ["topn", "yoy_mom"],
        datasource_dialect="clickhouse",
        matched_metrics=[{"name": "sales_amount"}],
    )

    assert "Relevant metric names from retrieval: sales_amount." in hint
    assert "TopN / ranking SQL guidance:" in hint
    assert "YoY / MoM SQL guidance:" in hint
    assert "dimension_col AS dimension_name" in hint
    assert "product_name" not in hint
    assert "fact_order_items" not in hint
    assert hint.count("LAG(metric_value, 12) OVER") == 1
    assert "prev_year_value" in hint
    assert "For ClickHouse monthly periods use toStartOfMonth(date_column)." in hint
    assert "toStartOfMonth(dd.date_value)" in hint


def test_build_olap_hint_uses_duckdb_month_expression():
    hint = build_olap_hint(["yoy_mom"], datasource_dialect="duckdb")

    assert "DATE_TRUNC('month', dd.date_value)" in hint
    assert "For DuckDB monthly periods use DATE_TRUNC('month', date_column)." in hint
    assert "toStartOfMonth" not in hint


def test_build_olap_hint_includes_moving_average_pattern():
    hint = build_olap_hint(["moving_avg"], datasource_dialect="duckdb")

    assert "Moving average SQL guidance:" in hint
    assert "AVG(metric_value) OVER" in hint
    assert "ROWS BETWEEN 6 PRECEDING AND CURRENT ROW" in hint


def test_describe_olap_intents_formats_empty_and_detected_intents():
    assert describe_olap_intents([]) == "未检测到 OLAP 分析意图"
    assert describe_olap_intents(["topn", "yoy_mom"]) == (
        "检测到 TopN / 排名 / 分层分析意图；检测到同比 / 环比分析意图"
    )
