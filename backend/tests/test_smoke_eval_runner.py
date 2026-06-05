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


def test_phase65_validations_record_check_results():
    result = runner.SmokeResult(
        case_id="phase65_case",
        case_type="normal",
        question="top products",
        tags=["phase65", "topn"],
        generated_sql="SELECT product_name, sales_amount FROM ranked ORDER BY sales_amount DESC LIMIT 10",
        normalized_sql="SELECT product_name, sales_amount FROM ranked ORDER BY sales_amount DESC LIMIT 10",
        olap_intents=["topn", "moving_avg"],
        plan_hints=["EXPLAIN 已生成，未发现明显性能风险。"],
    )

    expected = {
        "olap_intents": ["topn"],
        "required_sql_patterns": [r"ORDER\s+BY\s+sales_amount\s+DESC", r"LIMIT\s+10"],
        "plan_hints_exist": True,
    }
    runner._validate_olap_expectations(result, expected)
    runner._validate_required_sql_patterns(result, expected)
    runner._validate_plan_hint_expectation(result, expected)

    assert result.passed is True
    assert result.olap_intent_match is True
    assert result.sql_pattern_match is True
    assert result.plan_hint_match is True


def test_required_sql_patterns_can_be_case_sensitive():
    result = runner.SmokeResult(
        case_id="phase65_clickhouse_case",
        case_type="normal",
        question="ClickHouse yoy",
        tags=["phase65", "yoy_mom"],
        generated_sql="SELECT laginframe(sales_amount, 12) OVER (ORDER BY month_start) FROM t",
    )

    runner._validate_required_sql_patterns(
        result,
        {
            "required_sql_patterns": [
                {
                    "pattern": r"lagInFrame\s*\(\s*sales_amount\s*,\s*12\s*\)\s+OVER",
                    "case_sensitive": True,
                }
            ]
        },
    )

    assert result.passed is False
    assert result.sql_pattern_match is False
    assert result.error_category == "sql_generation_mismatch"


def test_render_report_includes_phase65_stats_by_datasource():
    result = runner.SmokeResult(
        case_id="phase65_case",
        case_type="normal",
        question="top products",
        tags=["phase65", "topn"],
        datasource_name="duckdb_ecommerce",
        datasource_dialect="duckdb",
        datasource_display_name="DuckDB (本地)",
        expected_olap_intents=["topn"],
        expected_sql_patterns=[r"LIMIT\s+10"],
        olap_intent_match=True,
        sql_pattern_match=True,
        chart_match=True,
        plan_hint_match=None,
    )

    report = runner._render_report([result], provider_name="mock", skipped_case_ids=[])

    assert "## Phase 6.5 OLAP Analytics" in report
    assert "| DuckDB (本地) | 1 | 1/1 (100.0%) | n/a | 1/1 (100.0%) | n/a | 1/1 (100.0%) | n/a |" in report
