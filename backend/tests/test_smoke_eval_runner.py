import importlib.util
import sys
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_smoke_eval.py"
SPEC = importlib.util.spec_from_file_location("run_smoke_eval", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_filter_cases_skips_unavailable_datasource():
    cases = [
        {"id": "duck", "question": "销售额"},
        {"id": "clickhouse", "question": "销售额", "datasource": "clickhouse_ecommerce"},
    ]
    available = {
        "duckdb_ecommerce": runner.DatasourceRef(
            name="duckdb_ecommerce",
            dialect="duckdb",
            display_name="DuckDB (本地)",
        )
    }

    selected, skipped = runner._filter_cases(
        cases,
        provider_name="mock",
        available_datasources=available,
    )

    assert [case["id"] for case in selected] == ["duck"]
    assert skipped == ["clickhouse (datasource unavailable: clickhouse_ecommerce)"]


def test_render_report_groups_cases_by_datasource():
    duckdb_result = runner.SmokeResult(
        case_id="duck_case",
        case_type="normal",
        question="DuckDB case",
        datasource_name="duckdb_ecommerce",
        datasource_dialect="duckdb",
        datasource_display_name="DuckDB (本地)",
    )
    clickhouse_result = runner.SmokeResult(
        case_id="clickhouse_case",
        case_type="normal",
        question="ClickHouse case",
        datasource_name="clickhouse_ecommerce",
        datasource_dialect="clickhouse",
        datasource_display_name="ClickHouse (OLAP)",
    )

    report = runner._render_report(
        [duckdb_result, clickhouse_result],
        provider_name="mock",
        skipped_case_ids=[],
    )

    assert "## Datasource Summary" in report
    assert "### DuckDB (本地) - 1 cases" in report
    assert "### ClickHouse (OLAP) - 1 cases" in report


def test_validate_dialect_hints_flags_duckdb_syntax():
    result = runner.SmokeResult(
        case_id="clickhouse_case",
        case_type="normal",
        question="ClickHouse case",
        datasource_name="clickhouse_ecommerce",
        datasource_dialect="clickhouse",
        datasource_display_name="ClickHouse (OLAP)",
        generated_sql="SELECT DATE_TRUNC('month', date_value) FROM dim_date",
    )

    runner._validate_dialect_hints(
        result,
        {"dialect_hints": [{"function": "toStartOfMonth"}, {"no_duckdb_syntax": True}]},
    )

    assert result.passed is False
    assert result.error_category == "dialect_mismatch"
