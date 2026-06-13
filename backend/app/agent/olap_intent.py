from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


OLAPIntentType = Literal["yoy_mom", "topn", "moving_avg"]
OLAP_INTENT_PRIORITY: tuple[OLAPIntentType, ...] = ("topn", "yoy_mom", "moving_avg")
INTENT_SCORE_THRESHOLD = 0.75


@dataclass(frozen=True)
class OLAPIntentScore:
    score: float
    signals: tuple[str, ...]


def _compile_patterns(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


_YOY_MOM_PATTERNS = (
    (
        "comparison_terms",
        _compile_patterns(
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
        ),
    ),
)
_CHINESE_NUMERAL = "一二三四五六七八九十百千万两"
_TOPN_PATTERNS = (
    (
        "ranking_terms",
        _compile_patterns(
            r"\btop(?:[\s-]*(?:\d+|n)\b|\b(?!-[A-Za-z]))",
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
        ),
    ),
)
_MOVING_AVG_PATTERNS = (
    (
        "moving_average_terms",
        _compile_patterns(
            r"移动平均",
            r"滚动平均",
            r"滑动平均",
            r"\d+\s*(日|天|周|月)\s*(移动|滚动|滑动)?\s*(平均|均值)",
            r"moving\s+average",
            r"rolling\s+average",
        ),
    ),
)
_PATTERNS_BY_INTENT: dict[OLAPIntentType, tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]] = {
    "topn": _TOPN_PATTERNS,
    "yoy_mom": _YOY_MOM_PATTERNS,
    "moving_avg": _MOVING_AVG_PATTERNS,
}
_YOY_MOM_PHRASES = (
    "same period last year",
    "same period previous year",
    "same month last year",
    "same quarter last year",
    "same week last year",
    "compared with last year",
    "compared to last year",
    "compare with last year",
    "compare to last year",
    "compared with previous month",
    "compared to previous month",
    "versus previous month",
    "vs previous month",
    "against prior period",
    "against previous period",
    "与去年同期比较",
    "和去年同期比较",
    "和去年同期对比",
    "与上月比较",
    "和上月比较",
    "和上月对比",
)
_MOVING_AVG_PHRASES = (
    "running average",
    "trailing average",
    "smoothed trend",
    "smooth trend",
    "滚动均值",
    "滑动均值",
    "移动均值",
    "平滑趋势",
    "均线",
)
_COMPARISON_TOKENS = {"compare", "compared", "comparison", "versus", "vs", "against"}
_PERIOD_ANCHOR_TOKENS = {"previous", "prior", "last"}
_PERIOD_TOKENS = {"period", "year", "month", "week", "quarter"}
_RANKING_TOKENS = {"rank", "ranking", "leaderboard"}
_RANKING_RESULT_TOKENS = {"top", "bottom", "first", "last"}
_BEST_WORST_TOKENS = {"best", "worst", "highest", "lowest", "largest", "smallest", "most", "least"}
_RANKING_GROUP_TOKENS = {
    "category",
    "categories",
    "channel",
    "channels",
    "customer",
    "customers",
    "item",
    "items",
    "product",
    "products",
    "region",
    "regions",
    "segment",
    "segments",
    "sku",
    "skus",
    "store",
    "stores",
    "user",
    "users",
}
_RANKING_CONTEXT_TOKENS = _RANKING_GROUP_TOKENS | {"performing", "performer", "performers"}
_AVERAGE_WINDOW_TOKENS = {"moving", "rolling", "running", "trailing"}
_AVERAGE_METRIC_TOKENS = {"average", "avg", "mean"}
_SMOOTHING_TOKENS = {"smooth", "smoothed", "smoothing"}
_TREND_TOKENS = {"trend", "trends", "series"}
_INTENT_DESCRIPTIONS: dict[str, str] = {
    "topn": "检测到 TopN / 排名 / 分层分析意图",
    "yoy_mom": "检测到同比 / 环比分析意图",
    "moving_avg": "检测到移动平均分析意图",
}


def detect_olap_intents(question: str) -> list[OLAPIntentType]:
    scores = analyze_olap_intents(question)
    detected = {intent for intent, score in scores.items() if score.score >= INTENT_SCORE_THRESHOLD}
    return [intent for intent in OLAP_INTENT_PRIORITY if intent in detected]


def analyze_olap_intents(question: str) -> dict[OLAPIntentType, OLAPIntentScore]:
    normalized_question = question.strip()
    compact_question = _compact_text(question)
    tokens = set(_ascii_tokens(question))
    scores: dict[OLAPIntentType, OLAPIntentScore] = {}
    for intent in OLAP_INTENT_PRIORITY:
        score, signals = _regex_score(intent, normalized_question)
        lexical_score, lexical_signals = _lexical_score(intent, compact_question, tokens)
        score = max(score, lexical_score)
        scores[intent] = OLAPIntentScore(score=score, signals=tuple((*signals, *lexical_signals)))
    return scores


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


def _regex_score(intent: OLAPIntentType, question: str) -> tuple[float, tuple[str, ...]]:
    signals = []
    for label, patterns in _PATTERNS_BY_INTENT[intent]:
        if any(pattern.search(question) for pattern in patterns):
            signals.append(f"regex:{label}")
    return (1.0 if signals else 0.0), tuple(signals)


def _lexical_score(
    intent: OLAPIntentType,
    compact_question: str,
    tokens: set[str],
) -> tuple[float, tuple[str, ...]]:
    if intent == "topn":
        return _topn_lexical_score(tokens)
    if intent == "yoy_mom":
        return _yoy_mom_lexical_score(compact_question, tokens)
    if intent == "moving_avg":
        return _moving_avg_lexical_score(compact_question, tokens)
    return 0.0, ()


def _topn_lexical_score(tokens: set[str]) -> tuple[float, tuple[str, ...]]:
    if tokens & _RANKING_TOKENS and tokens & (_RANKING_CONTEXT_TOKENS | _RANKING_RESULT_TOKENS):
        return 0.85, ("lexical:ranking_tokens",)
    if tokens & _BEST_WORST_TOKENS and tokens & _RANKING_CONTEXT_TOKENS:
        return 0.8, ("lexical:best_worst_context",)
    return 0.0, ()


def _yoy_mom_lexical_score(compact_question: str, tokens: set[str]) -> tuple[float, tuple[str, ...]]:
    if any(_compact_text(phrase) in compact_question for phrase in _YOY_MOM_PHRASES):
        return 0.9, ("lexical:comparison_phrase",)
    if tokens & _COMPARISON_TOKENS and tokens & _PERIOD_ANCHOR_TOKENS and tokens & _PERIOD_TOKENS:
        return 0.8, ("lexical:comparison_period_tokens",)
    if {"same", "period", "last", "year"}.issubset(tokens):
        return 0.8, ("lexical:same_period_last_year",)
    return 0.0, ()


def _moving_avg_lexical_score(compact_question: str, tokens: set[str]) -> tuple[float, tuple[str, ...]]:
    if any(_compact_text(phrase) in compact_question for phrase in _MOVING_AVG_PHRASES):
        return 0.9, ("lexical:moving_average_phrase",)
    if tokens & _AVERAGE_WINDOW_TOKENS and tokens & _AVERAGE_METRIC_TOKENS:
        return 0.85, ("lexical:average_window_tokens",)
    if tokens & _SMOOTHING_TOKENS and tokens & _TREND_TOKENS:
        return 0.8, ("lexical:smoothing_trend_tokens",)
    return 0.0, ()


def _compact_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _ascii_tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-zA-Z][a-zA-Z0-9_]*", text.casefold()))


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
            "Key rules:",
            "- Use ORDER BY metric_value DESC with LIMIT N for top rankings.",
            "- Use ORDER BY metric_value ASC with LIMIT N only when the user asks for bottom/least/lowest.",
            "- For tiering or segmentation, use CASE WHEN and GROUP BY the tier alias.",
            "- If percentage share is requested, compute the total with SUM(metric_value) OVER ().",
            "- Choose grouping dimensions and metric expressions from the schema context; do not invent columns.",
            "Pattern:",
            "SELECT dimension_name, metric_value",
            "FROM (",
            "  SELECT dimension_col AS dimension_name, SUM(metric_expression) AS metric_value",
            "  FROM source_tables",
            "  WHERE optional_filters",
            "  GROUP BY dimension_col",
            ") ranked",
            "ORDER BY metric_value DESC",
            "LIMIT N",
        ]
    )


def _yoy_mom_hint(datasource_dialect: str) -> str:
    month_expr = _month_period_expr(datasource_dialect)
    month_rule = _month_period_rule(datasource_dialect)
    return "\n".join(
        [
            "YoY / MoM SQL guidance:",
            "Key rules:",
            "- Use a subquery: aggregate by period first, then apply LAG() in the outer query.",
            "- Choose the date column, metric expression, source tables, and joins from the schema context.",
            "- When a business date dimension is present, use the join relationship provided in the schema context.",
            month_rule,
            "- MoM uses LAG(value, 1); monthly YoY uses LAG(value, 12); quarterly YoY uses LAG(value, 4).",
            "- Use NULLIF(previous_value, 0) for percentage change division.",
            "Pattern:",
            "SELECT",
            "  period,",
            "  metric_value,",
            "  prev_year_value,",
            "  ROUND((metric_value - prev_year_value) / NULLIF(prev_year_value, 0) * 100, 2) AS yoy_pct",
            "FROM (",
            "  SELECT",
            "    period,",
            "    metric_value,",
            "    LAG(metric_value, 12) OVER (ORDER BY period) AS prev_year_value",
            "  FROM (",
            "    SELECT",
            f"      {month_expr} AS period,",
            "      metric_expression AS metric_value",
            "    FROM source_tables",
            "    JOIN required_schema_joins",
            "    GROUP BY period",
            "  ) base",
            ") compared",
            "ORDER BY period",
        ]
    )


def _moving_avg_hint() -> str:
    return "\n".join(
        [
            "Moving average SQL guidance:",
            "Key rules:",
            "- Aggregate by date first, then compute AVG(metric_value) OVER in the outer query.",
            "- 7-day moving average uses ROWS BETWEEN 6 PRECEDING AND CURRENT ROW.",
            "- 30-day moving average uses ROWS BETWEEN 29 PRECEDING AND CURRENT ROW.",
            "Pattern:",
            "SELECT",
            "  period,",
            "  metric_value,",
            "  AVG(metric_value) OVER (ORDER BY period ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS ma_7d",
            "FROM (",
            "  SELECT date_column AS period, metric_expression AS metric_value",
            "  FROM source_tables",
            "  JOIN required_schema_joins",
            "  GROUP BY date_column",
            ") base",
            "ORDER BY period",
        ]
    )


def _month_period_expr(datasource_dialect: str) -> str:
    if datasource_dialect == "clickhouse":
        return "toStartOfMonth(date_column)"
    return "DATE_TRUNC('month', date_column)"


def _month_period_rule(datasource_dialect: str) -> str:
    if datasource_dialect == "clickhouse":
        return "- For ClickHouse monthly periods use toStartOfMonth(date_column)."
    return "- For DuckDB monthly periods use DATE_TRUNC('month', date_column)."
