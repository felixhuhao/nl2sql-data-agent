from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisSpace:
    name: str
    datasource: str
    tables: tuple[str, ...]
    enabled_metrics: tuple[str, ...]
    allowed_operations: tuple[str, ...]


ECOMMERCE_ANALYSIS_SPACE = AnalysisSpace(
    name="ecommerce_demo",
    datasource="duckdb_ecommerce",
    tables=(
        "fact_orders",
        "fact_order_items",
        "dim_date",
        "dim_users",
        "dim_products",
        "dim_regions",
        "dim_channels",
    ),
    enabled_metrics=("sales_amount", "order_count", "aov"),
    allowed_operations=("select",),
)


def get_default_analysis_space() -> AnalysisSpace:
    return ECOMMERCE_ANALYSIS_SPACE
