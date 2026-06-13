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

    overlay = semantic_overlay.load_semantic_overlay(manifest)

    assert overlay.table_semantics["fact_events"] == (
        "Event facts",
        "Event stream",
        "product",
    )
    assert overlay.table_column_semantics[("fact_events", "event_count")] == "Events counted from stream"
    assert overlay.metric_columns == {"event_count"}
    assert overlay.sample_value_fallbacks["event_type"] == ["signup"]
    assert overlay.confirmed_relationships == [
        (
            "fact_events",
            "product_id",
            "dim_products",
            "product_id",
            "many_to_one",
            "Events attach to products",
        )
    ]


def test_semantic_overlay_loader_normalizes_path_cache_keys(tmp_path):
    manifest = tmp_path / "overlay.yml"
    manifest.write_text("tables: {}\n", encoding="utf-8")
    semantic_overlay._load_overlay_by_path.cache_clear()

    data_from_str = semantic_overlay._load_overlay(str(manifest))
    data_from_path = semantic_overlay._load_overlay(manifest)

    assert data_from_path is data_from_str


def test_lazy_mapping_resolves_factory_once_for_common_dict_operations():
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return {"fact_events": ("Event facts", "Event stream", "product")}

    mapping = semantic_overlay._LazyMapping(factory)

    assert len(mapping) == 1
    assert list(mapping) == ["fact_events"]
    assert mapping["fact_events"] == ("Event facts", "Event stream", "product")
    assert calls == 1


def test_lazy_sequence_contains_uses_single_resolved_value():
    calls = 0
    relationship = ("fact_events", "product_id", "dim_products", "product_id", "many_to_one", "Products")

    def factory():
        nonlocal calls
        calls += 1
        return [relationship]

    sequence = semantic_overlay._LazySequence(factory)

    assert relationship in sequence
    assert relationship in sequence
    assert calls == 1


def test_lazy_value_can_be_reset_for_reload_paths():
    values = [{"first": 1}, {"second": 2}]

    lazy = semantic_overlay._LazyMapping(lambda: values.pop(0))

    assert list(lazy) == ["first"]
    lazy._reset()
    assert list(lazy) == ["second"]
