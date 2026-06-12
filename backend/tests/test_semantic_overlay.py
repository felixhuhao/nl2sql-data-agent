from backend.app.metadata import semantic_overlay


def test_semantic_overlay_loads_demo_manifest_with_legacy_shapes():
    assert semantic_overlay.TABLE_SEMANTICS["fact_orders"] == (
        "订单事实表",
        "订单支付金额、折扣和状态",
        "sales",
    )
    assert semantic_overlay.COLUMN_SEMANTICS["payment_amount"] == "订单实付金额，销售额口径字段"
    assert semantic_overlay.TABLE_COLUMN_SEMANTICS[("dim_products", "name")] == "商品名称"
    assert "date_value" in semantic_overlay.DIMENSION_COLUMNS
    assert "payment_amount" in semantic_overlay.METRIC_COLUMNS
    assert semantic_overlay.sample_value_fallbacks_json("gender") == '["女", "男"]'
    assert (
        "fact_order_items",
        "product_key",
        "dim_products",
        "product_key",
        "many_to_one",
        "明细关联商品",
    ) in semantic_overlay.CONFIRMED_RELATIONSHIPS


def test_semantic_overlay_can_load_an_alternate_manifest(tmp_path):
    manifest = tmp_path / "overlay.yml"
    manifest.write_text(
        """
tables:
  fact_events:
    display_name: Event facts
    description: Event stream
    domain: product
columns:
  event_count: Event count
table_columns:
  fact_events:
    event_count: Events counted from stream
dimension_columns:
  - event_day
metric_columns:
  - event_count
sample_value_fallbacks:
  event_type:
    - signup
confirmed_relationships:
  - source_table: fact_events
    source_column: product_id
    target_table: dim_products
    target_column: product_id
    relationship_type: many_to_one
    description: Events attach to products
""",
        encoding="utf-8",
    )

    data = semantic_overlay._load_overlay(str(manifest))

    assert semantic_overlay._table_semantics(data)["fact_events"] == (
        "Event facts",
        "Event stream",
        "product",
    )
    assert semantic_overlay._table_column_semantics(data)[("fact_events", "event_count")] == (
        "Events counted from stream"
    )
    assert semantic_overlay._string_set(data, "metric_columns") == {"event_count"}
    assert semantic_overlay._sample_value_fallbacks(data)["event_type"] == ["signup"]
    assert semantic_overlay._confirmed_relationships(data) == [
        (
            "fact_events",
            "product_id",
            "dim_products",
            "product_id",
            "many_to_one",
            "Events attach to products",
        )
    ]
