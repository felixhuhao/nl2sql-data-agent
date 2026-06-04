from __future__ import annotations

import re
from typing import Literal


OLAPIntentType = Literal["yoy_mom", "topn", "moving_avg"]
OLAP_INTENT_PRIORITY: tuple[OLAPIntentType, ...] = ("topn", "yoy_mom", "moving_avg")

_YOY_MOM_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"同比",
        r"环比",
        r"去年同期",
        r"较去年",
        r"比去年",
        r"较上月",
        r"比上月",
        r"较上周",
        r"比上周",
        r"month[-\s]?over[-\s]?month",
        r"year[-\s]?over[-\s]?year",
        r"\bmom\b",
        r"\byoy\b",
    )
)
_CHINESE_NUMERAL = "一二三四五六七八九十百千万两"
_TOPN_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\btop\s*(?:\d+|n)\b",
        rf"前\s*(?:\d+|[{_CHINESE_NUMERAL}]+)",
        rf"后\s*(?:\d+|[{_CHINESE_NUMERAL}]+)",
        r"排名",
        r"排行",
        r"最多",
        r"最少",
        r"最高",
        r"最低",
        r"分层",
        r"分群",
        r"高频用户",
        r"低频用户",
    )
)
_MOVING_AVG_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"移动平均",
        r"滚动平均",
        r"滑动平均",
        r"\d+\s*(日|天|周|月)\s*(移动|滚动|滑动)?\s*(平均|均值)",
        r"moving\s+average",
        r"rolling\s+average",
    )
)
_PATTERNS_BY_INTENT: dict[OLAPIntentType, tuple[re.Pattern[str], ...]] = {
    "topn": _TOPN_PATTERNS,
    "yoy_mom": _YOY_MOM_PATTERNS,
    "moving_avg": _MOVING_AVG_PATTERNS,
}
_INTENT_DESCRIPTIONS: dict[OLAPIntentType, str] = {
    "topn": "检测到 TopN / 排名 / 分层分析意图",
    "yoy_mom": "检测到同比 / 环比分析意图",
    "moving_avg": "检测到移动平均分析意图",
}


def detect_olap_intents(question: str) -> list[OLAPIntentType]:
    normalized_question = question.strip()
    detected = {
        intent
        for intent, patterns in _PATTERNS_BY_INTENT.items()
        if any(pattern.search(normalized_question) for pattern in patterns)
    }
    return [intent for intent in OLAP_INTENT_PRIORITY if intent in detected]


def build_olap_hint(
    intents: list[str],
    datasource_dialect: str,
    matched_metrics: list[dict] | None = None,
) -> str:
    if not intents:
        return ""

    sections = []
    metric_names = _matched_metric_names(matched_metrics or [])
    if metric_names:
        sections.append(f"Relevant metric names from retrieval: {', '.join(metric_names)}.")

    for intent in intents:
        if intent == "topn":
            sections.append(_topn_hint())
        elif intent == "yoy_mom":
            sections.append(_yoy_mom_hint(datasource_dialect))
        elif intent == "moving_avg":
            sections.append(_moving_avg_hint())
    return "\n\n".join(sections)


def describe_olap_intents(intents: list[str]) -> str:
    if not intents:
        return "未检测到 OLAP 分析意图"
    return "；".join(_INTENT_DESCRIPTIONS.get(intent, intent) for intent in intents)


def _matched_metric_names(metrics: list[dict]) -> list[str]:
    names = []
    for metric in metrics:
        name = metric.get("name")
        if name:
            names.append(str(name))
    return names


def _topn_hint() -> str:
    return "\n".join(
        [
            "TopN / ranking SQL guidance:",
            "- Use ORDER BY metric_value DESC with LIMIT N.",
            "- For tiering or segmentation, use CASE WHEN and GROUP BY the tier alias.",
            "- If percentage share is requested, compute the total with SUM(metric_value) OVER ().",
        ]
    )


def _yoy_mom_hint(datasource_dialect: str) -> str:
    date_function = "toStartOfMonth(dim_date.date_value)" if datasource_dialect == "clickhouse" else "DATE_TRUNC('month', dim_date.date_value)"
    return "\n".join(
        [
            "YoY / MoM SQL guidance:",
            "- Use a subquery: aggregate by period first, then apply LAG() in the outer query.",
            f"- Use {date_function} as the month period when monthly comparison is requested.",
            "- Join fact_orders.date_key to dim_date.date_key when using business dates.",
            "- MoM uses LAG(value, 1); monthly YoY uses LAG(value, 12); quarterly YoY uses LAG(value, 4).",
            "- Use NULLIF(previous_value, 0) for percentage change division.",
        ]
    )


def _moving_avg_hint() -> str:
    return "\n".join(
        [
            "Moving average SQL guidance:",
            "- Aggregate by date first, then compute AVG(metric_value) OVER in the outer query.",
            "- 7-day moving average uses ROWS BETWEEN 6 PRECEDING AND CURRENT ROW.",
            "- 30-day moving average uses ROWS BETWEEN 29 PRECEDING AND CURRENT ROW.",
        ]
    )
