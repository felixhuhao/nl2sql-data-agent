from pydantic import BaseModel, Field

from backend.app.execution.runner import QueryResult


DATE_COLUMN_HINTS = ("date", "day", "month", "year")
METRIC_COLUMN_HINTS = ("amount", "count", "sales", "revenue", "aov", "quantity")


class ChartRecommendation(BaseModel):
    chart_type: str
    x_column: str | None = None
    y_columns: list[str] = Field(default_factory=list)
    reason: str


def recommend_chart(result: QueryResult) -> ChartRecommendation:
    if not result.columns:
        return _table("No columns returned.")

    x_column = _find_time_column(result.columns)
    y_columns = _find_metric_columns(result.columns, exclude=x_column)
    if x_column is not None and y_columns:
        return ChartRecommendation(
            chart_type="line",
            x_column=x_column,
            y_columns=y_columns,
            reason="Detected a time column and numeric metric columns.",
        )

    return _table("No supported chart pattern detected.")


def _find_time_column(columns: list[str]) -> str | None:
    for column in columns:
        lower_column = column.lower()
        if any(hint in lower_column for hint in DATE_COLUMN_HINTS):
            return column
    return None


def _find_metric_columns(columns: list[str], exclude: str | None) -> list[str]:
    return [
        column
        for column in columns
        if column != exclude and any(hint in column.lower() for hint in METRIC_COLUMN_HINTS)
    ]


def _table(reason: str) -> ChartRecommendation:
    return ChartRecommendation(chart_type="table", reason=reason)
