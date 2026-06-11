from __future__ import annotations

import re
from datetime import date, timedelta


_RELATIVE_DATE_MATCHERS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("最近7天", "7d", re.compile(r"最近\s*7\s*天")),
    ("过去7天", "7d", re.compile(r"过去\s*7\s*天")),
    ("last 7 days", "7d", re.compile(r"\blast\s+7\s+days\b", re.IGNORECASE)),
    ("最近30天", "30d", re.compile(r"最近\s*30\s*天")),
    ("过去30天", "30d", re.compile(r"过去\s*30\s*天")),
    ("last 30 days", "30d", re.compile(r"\blast\s+30\s+days\b", re.IGNORECASE)),
    ("本月", "month", re.compile(r"本月(?![份度报])")),
    ("this month", "month", re.compile(r"\bthis\s+month\b", re.IGNORECASE)),
)


def build_relative_date_rules(dataset_current_date: str) -> dict[str, dict[str, str]]:
    current_date = date.fromisoformat(dataset_current_date)
    month_start = current_date.replace(day=1)
    ranges = {
        "7d": _rolling_window(current_date, 7),
        "30d": _rolling_window(current_date, 30),
        "month": {"start": month_start.isoformat(), "end": current_date.isoformat()},
    }
    return {phrase: dict(ranges[range_key]) for phrase, range_key, _pattern in _RELATIVE_DATE_MATCHERS}


def match_relative_date_rule(question: str, dataset_current_date: str) -> dict[str, str] | None:
    date_rules = build_relative_date_rules(dataset_current_date)
    for phrase, _range_key, pattern in _RELATIVE_DATE_MATCHERS:
        if pattern.search(question):
            date_range = date_rules[phrase]
            return {"phrase": phrase, **date_range}
    return None


def _rolling_window(current_date: date, days: int) -> dict[str, str]:
    return {
        "start": (current_date - timedelta(days=days - 1)).isoformat(),
        "end": current_date.isoformat(),
    }
