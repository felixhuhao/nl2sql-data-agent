import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


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


def test_case_datasources_preserves_legacy_scalar_and_validates_conflict():
    assert runner._case_datasources({"id": "legacy", "datasource": "clickhouse_ecommerce"}) == [
        "clickhouse_ecommerce"
    ]
    assert runner._case_datasources(
        {"id": "plural", "datasources": ["duckdb_ecommerce", "clickhouse_ecommerce"]}
    ) == ["duckdb_ecommerce", "clickhouse_ecommerce"]

    try:
        runner._case_datasources(
            {
                "id": "bad",
                "datasource": "duckdb_ecommerce",
                "datasources": ["clickhouse_ecommerce"],
            }
        )
    except ValueError as exc:
        assert "cannot set both" in str(exc)
    else:
        raise AssertionError("expected datasource/datasources conflict to fail")


def test_filter_cases_expands_plural_datasources():
    cases = [
        {
            "id": "parity",
            "question": "销售额",
            "datasources": ["duckdb_ecommerce", "clickhouse_ecommerce"],
        }
    ]
    available = {
        "duckdb_ecommerce": runner.DatasourceRef(
            name="duckdb_ecommerce",
            dialect="duckdb",
            display_name="DuckDB (本地)",
        ),
        "clickhouse_ecommerce": runner.DatasourceRef(
            name="clickhouse_ecommerce",
            dialect="clickhouse",
            display_name="ClickHouse (OLAP)",
        ),
    }

    selected, skipped = runner._filter_cases(
        cases,
        provider_name="mock",
        available_datasources=available,
    )

    assert skipped == []
    assert [case["_selected_datasource"] for case in selected] == [
        "duckdb_ecommerce",
        "clickhouse_ecommerce",
    ]


def test_require_clickhouse_detects_unavailable_clickhouse_skip():
    assert runner._has_unavailable_clickhouse_skip(
        ["case (datasource unavailable: clickhouse_ecommerce)"]
    )
    assert not runner._has_unavailable_clickhouse_skip(["case (provider=real)"])
    assert not runner._has_unavailable_clickhouse_skip(
        ["case (datasource unavailable: not_clickhouse_ecommerce)"]
    )


def test_validate_coverage_expectations_checks_pre_and_post_fields():
    result = runner.SmokeResult(
        case_id="coverage_case",
        case_type="normal",
        question="销售额",
        retrieval_pre_coverage={"band": "low"},
        retrieval_coverage={"band": "high", "expanded": True, "fallback_used": False},
    )

    runner._validate_coverage_expectations(
        result,
        {
            "pre_band": "low",
            "post_band": "high",
            "expanded": True,
            "fallback_used": False,
        },
    )

    assert result.passed is True


def test_validate_coverage_expectations_fails_mismatch():
    result = runner.SmokeResult(
        case_id="coverage_case",
        case_type="normal",
        question="销售额",
        retrieval_pre_coverage={"band": "high"},
        retrieval_coverage={"band": "high", "expanded": False, "fallback_used": False},
    )

    runner._validate_coverage_expectations(result, {"pre_band": "low", "expanded": True})

    assert result.passed is False
    assert result.error_category == "retrieval_coverage_mismatch"


def test_validate_parity_anchor_results_fails_band_divergence():
    duckdb_result = runner.SmokeResult(
        case_id="parity",
        case_type="normal",
        question="销售额",
        datasource_name="duckdb_ecommerce",
        retrieval_coverage={"band": "high"},
    )
    clickhouse_result = runner.SmokeResult(
        case_id="parity",
        case_type="normal",
        question="销售额",
        datasource_name="clickhouse_ecommerce",
        retrieval_coverage={"band": "low"},
    )

    runner._validate_parity_anchor_results([duckdb_result, clickhouse_result])

    assert duckdb_result.passed is False
    assert clickhouse_result.passed is False
    assert duckdb_result.error_category == "retrieval_coverage_mismatch"


def test_validate_parity_anchor_results_fails_missing_band():
    duckdb_result = runner.SmokeResult(
        case_id="parity",
        case_type="normal",
        question="销售额",
        datasource_name="duckdb_ecommerce",
        retrieval_coverage={"band": "high"},
    )
    clickhouse_result = runner.SmokeResult(
        case_id="parity",
        case_type="normal",
        question="销售额",
        datasource_name="clickhouse_ecommerce",
        retrieval_coverage=None,
    )

    runner._validate_parity_anchor_results([duckdb_result, clickhouse_result])

    assert duckdb_result.passed is False
    assert clickhouse_result.passed is False
    assert "missing coverage band" in duckdb_result.messages[0]


def test_validate_parity_anchor_results_skips_when_all_bands_missing():
    duckdb_result = runner.SmokeResult(
        case_id="parity",
        case_type="normal",
        question="销售额",
        datasource_name="duckdb_ecommerce",
        retrieval_coverage=None,
    )
    clickhouse_result = runner.SmokeResult(
        case_id="parity",
        case_type="normal",
        question="销售额",
        datasource_name="clickhouse_ecommerce",
        retrieval_coverage=None,
    )

    runner._validate_parity_anchor_results([duckdb_result, clickhouse_result])

    assert duckdb_result.passed is True
    assert clickhouse_result.passed is True


def test_retrieval_calibration_row_tracks_recovery_and_high_conf_regression():
    recovery = runner.SmokeResult(
        case_id="recover",
        case_type="normal",
        question="销量",
        tags=["retrieval_closeout", "missing_join_path"],
        passed=True,
        retrieval_pre_coverage={"band": "low"},
        retrieval_coverage={"band": "high", "expanded": True, "fallback_used": False},
        flags_on_context_delta=10,
    )
    fallback = runner.SmokeResult(
        case_id="fallback",
        case_type="normal",
        question="渠道",
        tags=["retrieval_closeout", "dangling_no_fact"],
        passed=True,
        retrieval_pre_coverage={"band": "low"},
        retrieval_coverage={"band": "low", "expanded": True, "fallback_used": True},
        retrieval_fallback_used=True,
        flags_on_context_delta=100,
    )
    high_conf = runner.SmokeResult(
        case_id="high_conf",
        case_type="normal",
        question="销售额",
        tags=[],
        passed=True,
        retrieval_reference_coverage={"band": "high"},
        retrieval_pre_coverage={"band": "high"},
        retrieval_coverage={"band": "high", "expanded": False, "fallback_used": False},
        flags_on_context_delta=0,
    )
    boundary_regression = runner.SmokeResult(
        case_id="boundary_regression",
        case_type="normal",
        question="销售额",
        tags=[],
        passed=True,
        retrieval_reference_coverage={"band": "high"},
        retrieval_pre_coverage={"band": "low"},
        retrieval_coverage={"band": "high", "expanded": True, "fallback_used": False},
        flags_on_context_delta=5,
    )

    row = runner._retrieval_calibration_row(
        0.8,
        [recovery, fallback, high_conf, boundary_regression],
    )

    assert row["recovery_passed"] == 1
    assert row["fallback_path_passed"] == 1
    assert row["high_conf_regressions"] == 1
    assert row["high_conf_regression_cases"] == ["boundary_regression"]


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


def test_render_report_includes_coverage_transition_and_context_delta():
    result = runner.SmokeResult(
        case_id="coverage_case",
        case_type="normal",
        question="销售额",
        retrieval_pre_coverage={"band": "low", "score": 0.5},
        retrieval_coverage={"band": "high", "score": 1.0, "expanded": True, "fallback_used": False},
        focused_context_chars=150,
        flags_off_context_chars=100,
        flags_on_context_delta=50,
    )

    report = runner._render_report([result], provider_name="mock", skipped_case_ids=[])

    assert "Avg flags-off focused context chars" in report
    assert "low/0.50 expanded=None fallback=None -> high/1.00 expanded=True fallback=False" in report
    assert "100->150 (+50)" in report


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


def test_compare_query_results_ignores_aliases_and_row_order():
    actual = SimpleNamespace(
        columns=["channel_name", "active_users"],
        rows=[["官网", 2], ["抖音", 1]],
        row_count=2,
    )
    expected = SimpleNamespace(
        columns=["channel_name", "user_count"],
        rows=[["抖音", 1], ["官网", 2]],
        row_count=2,
    )

    assert runner._compare_query_results(actual, expected) == {"match": True, "reason": ""}


def test_compare_query_results_detects_different_values():
    actual = SimpleNamespace(columns=["aov"], rows=[[12.34567]], row_count=1)
    expected = SimpleNamespace(columns=["aov"], rows=[[99.99]], row_count=1)

    comparison = runner._compare_query_results(actual, expected)

    assert comparison["match"] is False
    assert "row 0 differs" in comparison["reason"]


def test_compare_query_results_allows_extra_actual_columns_by_name():
    actual = SimpleNamespace(
        columns=["order_id", "date_key", "payment_amount"],
        rows=[["O-001", 20251231, 18.5], ["O-002", 20251230, 21.0]],
        row_count=2,
    )
    expected = SimpleNamespace(
        columns=["order_id", "payment_amount"],
        rows=[["O-002", 21.0], ["O-001", 18.5]],
        row_count=2,
    )

    assert runner._compare_query_results(actual, expected) == {"match": True, "reason": ""}


def test_compare_query_results_allows_missing_reference_identifier_columns():
    actual = SimpleNamespace(
        columns=["user_name", "order_count"],
        rows=[["张三", 5], ["李四", 4]],
        row_count=2,
    )
    expected = SimpleNamespace(
        columns=["user_id", "user_name", "order_count"],
        rows=[["U001", "李四", 4], ["U002", "张三", 5]],
        row_count=2,
    )

    assert runner._compare_query_results(actual, expected) == {"match": True, "reason": ""}


def test_real_provider_reference_match_skips_sql_shape_checks():
    result = runner.SmokeResult(
        case_id="real_case",
        case_type="normal",
        question="客单价",
    )
    query_result = SimpleNamespace(columns=["avg"], rows=[[10]], row_count=1)

    runner._validate_normal_case(
        result=result,
        expected={
            "result_columns": ["aov"],
            "required_tables": ["dim_date"],
            "required_columns": ["dim_date.date_value"],
            "join_paths": ["fact_orders.date_key -> dim_date.date_key"],
        },
        matched_query_id=None,
        query_result=query_result,
        explainability={"matched_tables": ["fact_orders"], "matched_columns": ["payment_amount"], "join_paths": []},
        chart_type="table",
        provider_name="deepseek",
        reference_result_match=True,
    )

    assert result.passed is True
