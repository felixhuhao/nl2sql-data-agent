from backend.app.agent.schema_evidence import build_schema_evidence


def _fake_sources():
    tables = [{"table_name": "fact_orders", "display_name": "订单", "description": "订单事实表", "domain": "sales"}]
    columns = {
        "fact_orders": [
            {"column_name": "order_status", "description": "订单状态", "sample_values": '["paid", "completed", "refunded"]'},
            {"column_name": "payment_amount", "description": "支付金额", "sample_values": []},
        ]
    }
    metrics = [{"name": "refund_rate", "label": "退款率", "expression": "x", "description": ""}]
    aliases = [{"alias": "金额", "column_name": "payment_amount", "table_name": "fact_orders"}]
    verified = [{"question": "查询每日销售额", "sql": "SELECT 1"}]
    return tables, columns, metrics, aliases, verified


def test_build_schema_evidence_indexes_all_channels():
    tables, columns, metrics, aliases, verified = _fake_sources()
    evidence = build_schema_evidence(
        "duckdb_ecommerce",
        list_tables=lambda datasource_name: tables,
        list_columns=lambda table_name, datasource_name: columns.get(table_name, []),
        list_metrics=lambda datasource_name: metrics,
        list_aliases=lambda datasource_name: aliases,
        list_verified_queries=lambda datasource_name: verified,
    )

    assert evidence.has_concept_evidence("退款率") is True
    assert evidence.has_concept_evidence("订单状态") is True
    assert evidence.has_concept_evidence("删除率") is False
    assert evidence.has_concept_evidence("id") is False
    assert evidence.column_values("order_status") == ("paid", "completed", "refunded")
    assert evidence.columns_with_value("refunded") == ("order_status",)
    assert evidence.columns_with_value("cancelled") == ()
    assert evidence.has_column("fact_orders", "order_status") is True
    assert evidence.unique_table_for_column("order_status") == "fact_orders"


def test_has_concept_evidence_is_normalization_insensitive():
    evidence = build_schema_evidence(
        "duckdb_ecommerce",
        list_tables=lambda datasource_name: [
            {"table_name": "fact_orders", "display_name": "Refund Rate", "description": "", "domain": ""}
        ],
        list_columns=lambda table_name, datasource_name: [],
        list_metrics=lambda datasource_name: [],
        list_aliases=lambda datasource_name: [],
        list_verified_queries=lambda datasource_name: [],
    )

    assert evidence.has_concept_evidence("refund   rate") is True


def test_has_concept_evidence_does_not_match_across_entries():
    evidence = build_schema_evidence(
        "duckdb_ecommerce",
        list_tables=lambda datasource_name: [
            {"table_name": "refund", "display_name": "", "description": "", "domain": ""}
        ],
        list_columns=lambda table_name, datasource_name: [
            {"column_name": "rate", "description": "", "sample_values": []},
        ],
        list_metrics=lambda datasource_name: [],
        list_aliases=lambda datasource_name: [],
        list_verified_queries=lambda datasource_name: [],
    )

    assert evidence.has_concept_evidence("refund rate") is False


def test_column_values_merge_duplicate_column_names_without_overwrite():
    evidence = build_schema_evidence(
        "duckdb_ecommerce",
        list_tables=lambda datasource_name: [
            {"table_name": "fact_orders", "display_name": "", "description": "", "domain": ""},
            {"table_name": "fact_returns", "display_name": "", "description": "", "domain": ""},
        ],
        list_columns=lambda table_name, datasource_name: {
            "fact_orders": [{"column_name": "status", "description": "", "sample_values": '["paid"]'}],
            "fact_returns": [{"column_name": "status", "description": "", "sample_values": '["refunded"]'}],
        }.get(table_name, []),
        list_metrics=lambda datasource_name: [],
        list_aliases=lambda datasource_name: [],
        list_verified_queries=lambda datasource_name: [],
    )

    assert evidence.column_values("status") == ("paid", "refunded")
    assert evidence.unique_table_for_column("status") is None
