from pydantic import BaseModel, Field

from backend.app.execution.runner import QueryResult


DATE_COLUMN_HINTS = ("date_value", "day", "month", "year")
METRIC_COLUMN_HINTS = ("amount", "count", "sales", "revenue", "aov", "quantity")
DETAIL_ROW_ID_COLUMNS = ("order_id", "item_id")
DETAIL_ROW_KEY_THRESHOLD = 3
DETAIL_ROW_COLUMN_THRESHOLD = 5
METRIC_SCALE_GAP_RATIO = 20.0


class ChartRecommendation(BaseModel):
    chart_type: str
    x_column: str | None = None
    y_columns: list[str] = Field(default_factory=list)
    reason: str


def recommend_chart(result: QueryResult) -> ChartRecommendation:
    if not result.columns:
        return _table("No columns returned.")
    if _looks_like_detail_rows(result):
        return _table("Result looks like record-level detail rows.")

    x_column = _find_time_column(result.columns)
    y_columns = _find_metric_columns(result.columns, exclude=x_column)
    if x_column is not None and y_columns:
        y_columns = _avoid_mixed_scale_metrics(result, y_columns)
        return ChartRecommendation(
            chart_type="line",
            x_column=x_column,
            y_columns=y_columns,
            reason="Detected a time column and numeric metric columns.",
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


def _find_time_column(columns: list[str]) -> str | None:
    for column in columns:
        lower_column = column.lower()
        if lower_column.endswith("_key"):
            continue
        if any(hint in lower_column for hint in DATE_COLUMN_HINTS):
            return column
    return None


def _find_metric_columns(columns: list[str], exclude: str | None) -> list[str]:
    return [
        column
        for column in columns
        if column != exclude and any(hint in column.lower() for hint in METRIC_COLUMN_HINTS)
    ]


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


def _table(reason: str) -> ChartRecommendation:
    return ChartRecommendation(chart_type="table", reason=reason)
