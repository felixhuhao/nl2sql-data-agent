import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from pydantic import BaseModel, Field

from backend.app.execution.runner import QueryResult


DATE_COLUMN_HINTS = ("date", "day", "week", "month", "year", "period")
METRIC_COLUMN_HINTS = (
    "amount",
    "count",
    "sales",
    "revenue",
    "aov",
    "quantity",
    "users",
    "value",
    "avg",
    "average",
    "ma",
    "share",
    "proportion",
    "contribution",
    "pct",
    "percent",
    "rate",
    "ratio",
    "growth",
    "change",
)
COMPARISON_ANCHOR_TOKENS = (
    "prev",
    "previous",
    "last",
    "prior",
    "yoy",
    "mom",
)
COMPARISON_DELTA_TOKENS = (
    "growth",
    "change",
    "delta",
)
COMPARISON_SCALE_TOKENS = (
    "pct",
    "percent",
    "rate",
    "ratio",
)
DISTRIBUTION_COLUMN_HINTS = (
    "share",
    "contribution",
    "proportion",
    "ratio",
    "percent",
    "percentage",
    "pct",
)
DETAIL_ROW_ID_COLUMNS = ("order_id", "item_id")
DETAIL_ROW_KEY_THRESHOLD = 3
DETAIL_ROW_COLUMN_THRESHOLD = 5
METRIC_SCALE_GAP_RATIO = 20.0
MAX_BAR_CATEGORIES = 30
MAX_PIE_CATEGORIES = 8
PROFILE_SIGNAL_THRESHOLD = 0.8


@dataclass(frozen=True)
class ColumnProfile:
    column: str
    non_null_count: int
    numeric_count: int
    temporal_count: int

    @property
    def numeric_ratio(self) -> float:
        return self.numeric_count / self.non_null_count if self.non_null_count else 0.0

    @property
    def temporal_ratio(self) -> float:
        return self.temporal_count / self.non_null_count if self.non_null_count else 0.0


class ChartRecommendation(BaseModel):
    chart_type: str
    x_column: str | None = None
    y_columns: list[str] = Field(default_factory=list)
    reason: str


def recommend_chart(
    result: QueryResult,
    olap_intents: list[str] | None = None,
) -> ChartRecommendation:
    if not result.columns:
        return _table("No columns returned.")
    if _looks_like_detail_rows(result):
        return _table("Result looks like record-level detail rows.")

    primary_intent = olap_intents[0] if olap_intents else None
    profiles = _column_profiles(result)
    x_column = _find_time_column(profiles)
    y_columns = _find_metric_columns(profiles, exclude=x_column)
    if primary_intent == "yoy_mom" and x_column is not None and len(y_columns) >= 2:
        dual_axis_columns = _select_dual_axis_columns(result, y_columns)
        if len(dual_axis_columns) >= 2:
            return ChartRecommendation(
                chart_type="dual_axis",
                x_column=x_column,
                y_columns=dual_axis_columns,
                reason="Year-over-year or month-over-month comparison detected.",
            )
    if primary_intent == "moving_avg" and x_column is not None and y_columns:
        y_columns = _avoid_mixed_scale_metrics(result, y_columns)
        return ChartRecommendation(
            chart_type="line",
            x_column=x_column,
            y_columns=y_columns,
            reason="Moving average analysis detected.",
        )

    if x_column is not None and y_columns:
        y_columns = _avoid_mixed_scale_metrics(result, y_columns)
        return ChartRecommendation(
            chart_type="line",
            x_column=x_column,
            y_columns=y_columns,
            reason="Detected a time column and numeric metric columns.",
        )

    category_column = _find_category_column(profiles, exclude=y_columns)
    if category_column is not None and y_columns and 0 < result.row_count <= MAX_BAR_CATEGORIES:
        if result.row_count == 1:
            return _table("Single category row is clearer as a table.")
        if primary_intent == "topn":
            y_columns = _avoid_mixed_scale_metrics(result, y_columns)
            return ChartRecommendation(
                chart_type="bar",
                x_column=category_column,
                y_columns=y_columns,
                reason="TopN analysis detected.",
            )
        pie_metric = _find_distribution_metric_column(y_columns)
        if pie_metric is not None and result.row_count <= MAX_PIE_CATEGORIES:
            return ChartRecommendation(
                chart_type="pie",
                x_column=category_column,
                y_columns=[pie_metric],
                reason="Distribution query with few categories.",
            )
        y_columns = _avoid_mixed_scale_metrics(result, y_columns)
        return ChartRecommendation(
            chart_type="bar",
            x_column=category_column,
            y_columns=y_columns,
            reason="Detected a category column and numeric metric columns.",
        )

    return _table("No supported chart pattern detected.")


def _looks_like_detail_rows(result: QueryResult) -> bool:
    lower_columns = [column.lower() for column in result.columns]
    has_record_id = any(column in DETAIL_ROW_ID_COLUMNS for column in lower_columns)
    key_column_count = sum(1 for column in lower_columns if column.endswith("_key"))
    return (
        len(result.columns) >= DETAIL_ROW_COLUMN_THRESHOLD
        and (has_record_id or key_column_count >= DETAIL_ROW_KEY_THRESHOLD)
    )


def _column_profiles(result: QueryResult) -> list[ColumnProfile]:
    profiles = []
    for index, column in enumerate(result.columns):
        values = tuple(row[index] for row in result.rows if len(row) > index)
        non_null_values = tuple(value for value in values if value is not None)
        profiles.append(
            ColumnProfile(
                column=column,
                non_null_count=len(non_null_values),
                numeric_count=sum(1 for value in non_null_values if _is_number(value)),
                temporal_count=sum(1 for value in non_null_values if _is_temporal_value(value)),
            )
        )
    return profiles


def _find_time_column(profiles: list[ColumnProfile]) -> str | None:
    for profile in profiles:
        if _is_time_profile(profile):
            return profile.column
    for profile in profiles:
        column = profile.column
        if _is_key_or_id_column(column):
            continue
        if _has_any_token(column, DATE_COLUMN_HINTS):
            return column
    return None


def _find_metric_columns(profiles: list[ColumnProfile], exclude: str | None) -> list[str]:
    return [
        profile.column
        for profile in profiles
        if profile.column != exclude and _is_metric_profile(profile)
    ]


def _select_dual_axis_columns(result: QueryResult, y_columns: list[str]) -> list[str]:
    comparison_columns = [column for column in y_columns if _is_comparison_column(column)]
    primary_columns = [column for column in y_columns if column not in comparison_columns]
    if not comparison_columns or not primary_columns:
        return []

    selected_columns = [primary_columns[0], _preferred_comparison_column(comparison_columns)]
    if not _has_mixed_scale(result, selected_columns) and not _has_scale_comparison_column(selected_columns):
        return []
    return selected_columns


def _is_comparison_column(column: str) -> bool:
    tokens = set(_column_tokens(column))
    if tokens & set(COMPARISON_ANCHOR_TOKENS):
        return True
    if tokens & set(COMPARISON_DELTA_TOKENS):
        return True
    return bool(tokens & set(COMPARISON_SCALE_TOKENS)) and bool(
        tokens & (set(COMPARISON_ANCHOR_TOKENS) | set(COMPARISON_DELTA_TOKENS))
    )


def _preferred_comparison_column(columns: list[str]) -> str:
    scale_columns = [
        column
        for column in columns
        if _has_any_token(column, (*COMPARISON_SCALE_TOKENS, *COMPARISON_DELTA_TOKENS))
    ]
    return (scale_columns or columns)[0]


def _has_scale_comparison_column(columns: list[str]) -> bool:
    return any(_has_any_token(column, COMPARISON_SCALE_TOKENS) for column in columns)


def _find_distribution_metric_column(y_columns: list[str]) -> str | None:
    for column in y_columns:
        if _is_comparison_column(column):
            continue
        if _has_any_token(column, DISTRIBUTION_COLUMN_HINTS):
            return column
    return None


def _find_category_column(profiles: list[ColumnProfile], exclude: list[str]) -> str | None:
    excluded_columns = set(exclude)
    for profile in profiles:
        column = profile.column
        if column in excluded_columns:
            continue
        if _is_key_or_id_column(column):
            continue
        if _is_time_profile(profile):
            continue
        if profile.non_null_count and profile.numeric_ratio < PROFILE_SIGNAL_THRESHOLD:
            return column
    return None


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(cast(Any, value))
    except (TypeError, ValueError):
        return False
    return True


def _is_temporal_value(value: object) -> bool:
    if isinstance(value, (datetime, date)):
        return True
    if not isinstance(value, str):
        return False
    value = value.strip()
    return bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?", value)
        or re.fullmatch(r"\d{4}-\d{2}", value)
    )


def _is_time_profile(profile: ColumnProfile) -> bool:
    column = profile.column
    if _is_key_or_id_column(column) or _is_comparison_column(column):
        return False
    if profile.temporal_ratio >= PROFILE_SIGNAL_THRESHOLD:
        return True
    return profile.non_null_count == 0 and _has_any_token(column, DATE_COLUMN_HINTS)


def _is_metric_profile(profile: ColumnProfile) -> bool:
    column = profile.column
    if _is_key_or_id_column(column) or _is_time_profile(profile):
        return False
    if profile.numeric_ratio >= PROFILE_SIGNAL_THRESHOLD:
        return True
    return _has_any_token(column, METRIC_COLUMN_HINTS)


def _is_key_or_id_column(column: str) -> bool:
    lower_column = column.lower()
    return lower_column.endswith("_key") or lower_column in DETAIL_ROW_ID_COLUMNS


def _avoid_mixed_scale_metrics(result: QueryResult, y_columns: list[str]) -> list[str]:
    if len(y_columns) <= 1:
        return y_columns

    magnitudes = [
        (column, _metric_magnitude(result, column))
        for column in y_columns
    ]
    usable_magnitudes = [(column, magnitude) for column, magnitude in magnitudes if magnitude is not None]
    if len(usable_magnitudes) <= 1:
        return y_columns

    positive_magnitudes = [(column, magnitude) for column, magnitude in usable_magnitudes if magnitude > 0]
    if len(positive_magnitudes) <= 1:
        return y_columns

    min_magnitude = min(magnitude for _, magnitude in positive_magnitudes)
    max_column, max_magnitude = max(usable_magnitudes, key=lambda item: item[1])
    if min_magnitude > 0 and max_magnitude / min_magnitude >= METRIC_SCALE_GAP_RATIO:
        return [max_column]
    return y_columns


def _metric_magnitude(result: QueryResult, column: str) -> float | None:
    column_index = result.columns.index(column)
    numeric_values = []
    for row in result.rows:
        try:
            numeric_values.append(abs(float(row[column_index])))
        except (TypeError, ValueError):
            continue
    if not numeric_values:
        return None
    return max(numeric_values)


def _has_mixed_scale(result: QueryResult, columns: list[str]) -> bool:
    magnitudes = [_metric_magnitude(result, column) for column in columns]
    positive_magnitudes = [magnitude for magnitude in magnitudes if magnitude is not None and magnitude > 0]
    if len(positive_magnitudes) <= 1:
        return False
    min_magnitude = min(positive_magnitudes)
    max_magnitude = max(positive_magnitudes)
    return max_magnitude / min_magnitude >= METRIC_SCALE_GAP_RATIO


def _has_any_token(column: str, hints: tuple[str, ...]) -> bool:
    tokens = set(_column_tokens(column))
    return bool(tokens & set(hints))


def _column_tokens(column: str) -> tuple[str, ...]:
    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", column)
    return tuple(
        part
        for part in re.split(r"[^A-Za-z0-9]+", snake_case.lower())
        if part
    )


def _table(reason: str) -> ChartRecommendation:
    return ChartRecommendation(chart_type="table", reason=reason)
