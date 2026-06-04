from __future__ import annotations

import re
from typing import Literal


OLAPIntentType = Literal["yoy_mom", "topn", "moving_avg"]
OLAP_INTENT_PRIORITY: tuple[OLAPIntentType, ...] = ("topn", "yoy_mom", "moving_avg")

_YOY_MOM_PATTERNS = (
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
_TOPN_PATTERNS = (
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\btop\s*\d*\b",
        r"top\s*n",
        r"前\s*\d+",
        r"后\s*\d+",
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
_MOVING_AVG_PATTERNS = (
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
    "topn": tuple(_TOPN_PATTERNS),
    "yoy_mom": tuple(_YOY_MOM_PATTERNS),
    "moving_avg": tuple(_MOVING_AVG_PATTERNS),
}
_INTENT_DESCRIPTIONS: dict[OLAPIntentType, str] = {
    "topn": "检测到 TopN / 排名 / 分层分析意图",
    "yoy_mom": "检测到同比 / 环比分析意图",
    "moving_avg": "检测到移动平均分析意图",
}


def detect_olap_intents(question: str, matched_metrics: list | None = None) -> list[OLAPIntentType]:
    del matched_metrics
    normalized_question = question.strip()
    detected = {
        intent
        for intent, patterns in _PATTERNS_BY_INTENT.items()
        if any(pattern.search(normalized_question) for pattern in patterns)
    }
    return [intent for intent in OLAP_INTENT_PRIORITY if intent in detected]


def describe_olap_intents(intents: list[str]) -> str:
    if not intents:
        return "未检测到 OLAP 分析意图"
    return "；".join(_INTENT_DESCRIPTIONS.get(intent, intent) for intent in intents)
