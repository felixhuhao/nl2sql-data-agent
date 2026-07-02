from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.metadata import retrieval, retrieval_coverage
from backend.app.metadata.models import MetaColumn, MetaRelationship, MetaTable, create_metadata_schema
from backend.app.metadata.seed import seed_semantics


def test_retrieve_assets_matches_verified_query_metrics_and_tables(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("查询最近30天每日销售额和订单数")

    assert result["fallback_used"] is False
    assert {"sales_amount", "order_count"}.issubset(set(_names(result["metrics"], "name")))
    assert "recent_30d_daily_sales" in _names(result["verified_queries"], "id")
    assert {"fact_orders", "dim_date"}.issubset(set(_names(result["tables"], "table_name")))
    assert ("fact_orders", "payment_amount") in _column_keys(result)
    assert ("fact_orders", "order_id") in _column_keys(result)


def test_retrieve_assets_matches_channel_aliases(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("按渠道统计最近30天销售额", use_vector=False)

    assert _item_by_name(result["tables"], "dim_channels", "table_name")["source"] == "lexical"
    assert _item_by_name(result["tables"], "fact_orders", "table_name")["source"] in {
        "lexical",
        "metric_expansion",
        "verified_query",
    }
    assert "sales_amount" in _names(result["metrics"], "name")
    assert "dim_channels" in _names(result["tables"], "table_name")
    assert "dim_date" in _names(result["tables"], "table_name")
    assert ("fact_orders", "channel_key") in _column_keys(result)
    assert ("dim_channels", "channel_name") in _column_keys(result)
    assert ("dim_date", "date_value") in _column_keys(result)


def test_retrieve_assets_matches_sales_share_intent(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("各渠道销售占比", use_vector=False)

    assert "sales_amount" in _names(result["metrics"], "name")
    assert "dim_channels" in _names(result["tables"], "table_name")
    assert "fact_orders" in _names(result["tables"], "table_name")
    assert ("dim_channels", "channel_name") in _column_keys(result)
    assert ("fact_orders", "payment_amount") in _column_keys(result)


def test_retrieve_assets_matches_sales_share_concept_without_metric_label(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("各渠道营收占比", use_vector=False)

    sales_amount = _item_by_name(result["metrics"], "sales_amount", "name")
    assert "metric_sales_share_intent" in sales_amount["reasons"]
    assert ("fact_orders", "payment_amount") in _column_keys(result)


def test_retrieve_assets_matches_metric_label(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("客单价", use_vector=False)

    assert result["metrics"][0]["name"] == "aov"
    assert _item_by_name(result["tables"], "fact_orders", "table_name")["source"] == "metric_expansion"
    assert "dim_date" not in _names(result["tables"], "table_name")


def test_retrieve_assets_skips_metric_time_column_without_time_intent(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("华东地区销售额", use_vector=False)

    assert "dim_date" not in _names(result["tables"], "table_name")
    assert ("dim_date", "date_value") not in _column_keys(result)
    assert all("metric_time_column:sales_amount" not in table["reasons"] for table in result["tables"])
    assert all("metric_time_column:sales_amount" not in column["reasons"] for column in result["columns"])


def test_retrieve_assets_preserves_standalone_recent_time_intent(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("最近销售额", use_vector=False)

    assert "dim_date" in _names(result["tables"], "table_name")
    assert ("dim_date", "date_value") in _column_keys(result)


def test_retrieve_assets_matches_sample_values(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("华东地区销售额")

    region_group = next(
        column
        for column in result["columns"]
        if column["table_name"] == "dim_regions" and column["column_name"] == "region_group"
    )
    assert "sample_value:华东" in region_group["reasons"]


def test_retrieve_assets_ignores_numeric_sample_values(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)
    _set_sample_values(engine, "dim_date", "date_key", "[3]")
    _set_sample_values(engine, "dim_channels", "channel_key", "[3]")

    result = retrieval.retrieve_metadata_assets("最近30天销售额", use_vector=False)

    assert ("dim_date", "date_key") not in _column_keys(result)
    assert ("dim_channels", "channel_key") not in _column_keys(result)
    assert all("sample_value:3" not in column["reasons"] for column in result["columns"])


def test_retrieve_assets_falls_back_to_allowed_tables_when_no_assets_match(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets("完全无法命中的问题")

    assert result["fallback_used"] is True
    assert set(_names(result["tables"], "table_name")) == {
        "fact_orders",
        "dim_date",
        "dim_regions",
        "dim_channels",
    }
    assert {table["source"] for table in result["tables"]} == {"fallback"}
    assert result["columns"] == []
    assert result["metrics"] == []
    assert result["verified_queries"] == []


def test_retrieve_assets_applies_per_type_limits(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)

    result = retrieval.retrieve_metadata_assets(
        "查询最近30天每日销售额和订单数",
        table_limit=1,
        column_limit=2,
        metric_limit=1,
        verified_query_limit=1,
    )

    assert len(result["tables"]) == 1
    assert len(result["columns"]) == 2
    assert len(result["metrics"]) == 1
    assert len(result["verified_queries"]) == 1


def test_retrieve_assets_handles_empty_analysis_space(monkeypatch):
    _patch_retrieval_db(monkeypatch)

    result = retrieval.retrieve_metadata_assets("销售额", use_vector=False)

    assert result["fallback_used"] is True
    assert result["tables"] == []
    assert result["columns"] == []
    assert result["metrics"] == []
    assert result["verified_queries"] == []


def test_retrieve_assets_can_force_vector_off(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)
    monkeypatch.setattr(retrieval, "get_settings", lambda: type("Settings", (), {"vector_enabled": True})())
    monkeypatch.setattr(
        retrieval,
        "hybrid_merge",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hybrid should not run")),
    )

    result = retrieval.retrieve_metadata_assets("销售额", use_vector=False)

    assert "sales_amount" in _names(result["metrics"], "name")


def test_retrieve_assets_uses_vector_before_fallback(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)
    monkeypatch.setattr(retrieval, "get_settings", lambda: type("Settings", (), {"vector_enabled": True})())

    def fake_hybrid(rule_result, question, **kwargs):
        return {
            **rule_result,
            "metrics": [{"name": "sales_amount", "score": 0.5, "reasons": ["vector:0.90"]}],
            "retrieval_meta": {"vector_used": True, "index_status": "ready", "sources": {}, "value_hits": []},
        }

    monkeypatch.setattr(retrieval, "hybrid_merge", fake_hybrid)

    result = retrieval.retrieve_metadata_assets("规则无法命中但向量命中", use_vector=None)

    assert result["fallback_used"] is False
    assert result["tables"] == []
    assert result["metrics"][0]["name"] == "sales_amount"


def test_query_profile_uses_cjk_ngrams_without_noisy_unigrams():
    terms = retrieval._query_profile("按渠道统计销售额").terms

    assert {"渠道", "销售", "销售额"}.issubset(terms)
    assert "渠" not in terms
    assert "销" not in terms


def test_ascii_term_variants_cover_common_morphology():
    terms = retrieval._query_profile("branches updated ranking sales").terms

    assert {"branch", "update", "rank", "sale"}.issubset(terms)


def test_score_coverage_bands_healthy_disconnected_weak_and_empty(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)
    monkeypatch.setattr(
        retrieval_coverage,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "retrieval_coverage_threshold": 0.7,
                "retrieval_coverage_strength_weight": 0.5,
                "retrieval_coverage_structural_weight": 0.5,
                "retrieval_fact_min_dim_edges": 2,
            },
        )(),
    )

    healthy = retrieval_coverage.score_coverage(
        {
            "tables": [
                {"table_name": "fact_orders", "score": 30},
                {"table_name": "dim_channels", "score": 10},
            ],
            "columns": [],
            "metrics": [{"name": "sales_amount", "score": 30}],
            "verified_queries": [],
        }
    )
    disconnected = retrieval_coverage.score_coverage(
        {
            "tables": [
                {"table_name": "dim_channels", "score": 30},
                {"table_name": "dim_regions", "score": 30},
            ],
            "columns": [],
            "metrics": [{"name": "sales_amount", "score": 30}],
            "verified_queries": [],
        }
    )
    weak_connected = retrieval_coverage.score_coverage(
        {
            "tables": [
                {"table_name": "fact_orders", "score": 3},
                {"table_name": "dim_channels", "score": 3},
            ],
            "columns": [],
            "metrics": [{"name": "sales_amount", "score": 3}],
            "verified_queries": [],
        }
    )
    empty = retrieval_coverage.score_coverage({"tables": [], "columns": [], "metrics": [], "verified_queries": []})

    assert healthy.band == "high"
    assert healthy.structural_score == 1.0
    assert disconnected.band == "low"
    assert disconnected.structural_score == 0.0
    assert weak_connected.band == "low"
    assert weak_connected.signals["join_connected"] is True
    assert empty.band == "low"


def test_expand_via_graph_skips_high_fanout_caps_and_scopes(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    _insert_demo_physical_metadata(engine)
    with Session(engine) as session:
        session.add(
            MetaRelationship(
                source_table="dim_channels",
                source_column="channel_key",
                target_table="dim_regions",
                target_column="region_key",
                relationship_type="many_to_one",
                confidence=1.0,
                fanout_risk="high",
            )
        )
        session.commit()
    monkeypatch.setattr(
        retrieval_coverage,
        "get_settings",
        lambda: type("Settings", (), {"retrieval_expansion_max_tables": 1})(),
    )

    result = retrieval_coverage.expand_via_graph(
        {
            "tables": [{"table_name": "dim_channels", "score": 30}],
            "columns": [],
            "metrics": [],
            "verified_queries": [],
        }
    )

    assert "fact_orders" in _names(result["tables"], "table_name")
    assert "dim_regions" not in _names(result["tables"], "table_name")
    assert ("fact_orders", "channel_key") in _column_keys(result)
    assert ("dim_channels", "channel_key") in _column_keys(result)
    assert result["retrieval_coverage"]["expanded"] is True


def test_expand_via_graph_no_relationships_noops(monkeypatch):
    engine = _patch_retrieval_db(monkeypatch)
    with Session(engine) as session:
        table = MetaTable(table_name="lonely_table", enabled=True)
        session.add(table)
        session.commit()
    monkeypatch.setattr(
        retrieval_coverage,
        "get_settings",
        lambda: type("Settings", (), {"retrieval_expansion_max_tables": 1})(),
    )

    original = {"tables": [{"table_name": "lonely_table", "score": 30}], "columns": []}
    result = retrieval_coverage.expand_via_graph(original)

    assert result == original


def _patch_retrieval_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)

    @contextmanager
    def session_scope():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(retrieval, "get_sqlite_engine", lambda: engine)
    monkeypatch.setattr(retrieval, "sqlite_session", session_scope)
    monkeypatch.setattr(retrieval_coverage, "get_sqlite_engine", lambda: engine)
    monkeypatch.setattr(retrieval_coverage, "sqlite_session", session_scope)
    return engine


def _insert_demo_physical_metadata(engine) -> None:
    with Session(engine) as session:
        table_columns = {
            "fact_orders": [
                "order_id",
                "payment_amount",
                "date_key",
                "region_key",
                "channel_key",
            ],
            "dim_date": ["date_key", "date_value"],
            "dim_regions": ["region_key", "region_group"],
            "dim_channels": ["channel_key", "channel_name"],
        }
        for table_name, column_names in table_columns.items():
            table = MetaTable(table_name=table_name, enabled=True)
            session.add(table)
            session.flush()
            session.add_all(
                [
                    MetaColumn(table_id=table.id, column_name=column_name, data_type="VARCHAR")
                    for column_name in column_names
                ]
            )
        seed_semantics(session)
        session.commit()


def _set_sample_values(engine, table_name: str, column_name: str, sample_values: str) -> None:
    with Session(engine) as session:
        column = session.scalar(
            select(MetaColumn)
            .join(MetaTable)
            .where(MetaTable.table_name == table_name, MetaColumn.column_name == column_name)
        )
        assert column is not None
        column.sample_values = sample_values
        session.commit()


def _names(items: list[dict], key: str) -> list[str]:
    return [item[key] for item in items]


def _item_by_name(items: list[dict], value: str, key: str) -> dict:
    return next(item for item in items if item[key] == value)


def _column_keys(result: dict) -> set[tuple[str, str]]:
    return {(column["table_name"], column["column_name"]) for column in result["columns"]}
