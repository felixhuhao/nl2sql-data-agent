from backend.app.agent.olap_intent import detect_olap_intents, describe_olap_intents


def test_detect_olap_intents_returns_empty_for_basic_query():
    assert detect_olap_intents("查询最近30天每日销售额和订单数") == []


def test_detect_olap_intents_returns_priority_order_for_composite_question():
    assert detect_olap_intents("查询销售额前10的商品同比增长") == ["topn", "yoy_mom"]


def test_detect_olap_intents_detects_moving_average():
    assert detect_olap_intents("查询最近30天销售额7日移动平均") == ["moving_avg"]


def test_describe_olap_intents_formats_empty_and_detected_intents():
    assert describe_olap_intents([]) == "未检测到 OLAP 分析意图"
    assert describe_olap_intents(["topn", "yoy_mom"]) == (
        "检测到 TopN / 排名 / 分层分析意图；检测到同比 / 环比分析意图"
    )
