from types import SimpleNamespace

from backend.app.config import DEFAULT_EMBEDDING_MODEL
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.vector import searcher
from backend.app.metadata.vector.store import VectorIndexStatus, VectorSearchHit


def test_retrieve_vector_assets_returns_disabled_when_vector_disabled(monkeypatch):
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(vector_enabled=False))
    monkeypatch.setattr(
        searcher,
        "get_vector_store",
        lambda: (_ for _ in ()).throw(AssertionError("store should not be loaded")),
    )

    result = searcher.retrieve_vector_assets("销售额")

    assert result.vector_used is False
    assert result.index_status == "disabled"


def test_retrieve_vector_assets_auto_uses_default_embedding_model_when_config_blank(monkeypatch):
    fake_store = FakeVectorStore(status=VectorIndexStatus(status="missing", stale_reason="Missing index metadata."))
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(vector_enabled="auto", embedding_model=None))
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)

    result = searcher.retrieve_vector_assets("销售额")

    assert result.vector_used is False
    assert result.index_status == "missing"
    assert result.stale_reason == "Missing index metadata."
    assert fake_store.expected_model == DEFAULT_EMBEDDING_MODEL
    assert fake_store.expected_dimension is None


def test_retrieve_vector_assets_auto_falls_back_when_qdrant_unavailable(monkeypatch):
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(vector_enabled="auto"))
    monkeypatch.setattr(
        searcher,
        "get_vector_store",
        lambda: (_ for _ in ()).throw(RuntimeError("Qdrant down")),
    )

    result = searcher.retrieve_vector_assets("销售额")

    assert result.vector_used is False
    assert result.index_status == "disabled"
    assert result.stale_reason == "Vector index is unavailable: Qdrant down"


def test_retrieve_vector_assets_returns_stale_status(monkeypatch):
    fake_store = FakeVectorStore(status=VectorIndexStatus(status="stale", stale_reason="old model"))
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings())
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)

    result = searcher.retrieve_vector_assets("销售额")

    assert result.vector_used is False
    assert result.index_status == "stale"
    assert result.stale_reason == "old model"
    assert fake_store.search_calls == []


def test_retrieve_vector_assets_searches_ready_index_and_filters_threshold(monkeypatch):
    fake_store = FakeVectorStore(
        status=VectorIndexStatus(status="ready"),
        hits={
            "metric_vectors": [
                VectorSearchHit("metric", "sales_amount", "销售额", 0.1, 0.9, {"name": "sales_amount"}),
                VectorSearchHit("metric", "weak_metric", "弱匹配", 1.0, 0.4, {"name": "weak_metric"}),
            ],
            "table_vectors": [
                VectorSearchHit("table", "fact_orders", "订单", 0.2, 0.8, {"table_name": "fact_orders"}),
            ],
        },
    )
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(threshold=0.7))
    monkeypatch.setattr(searcher, "embed_text", lambda question: [0.1, 0.2, 0.3])
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)

    result = searcher.retrieve_vector_assets("营收总额")

    assert result.vector_used is True
    assert result.index_status == "ready"
    assert [hit.asset_id for hit in result.hits["metrics"]] == ["sales_amount"]
    assert [hit.asset_id for hit in result.hits["tables"]] == ["fact_orders"]
    assert {call[0] for call in fake_store.search_calls} == {
        "table_vectors",
        "column_vectors",
        "metric_vectors",
        "verified_query_vectors",
    }
    assert all(call[3] == _datasource_filter() for call in fake_store.search_calls)


def test_search_values_prefers_exact_and_deduplicates(monkeypatch):
    fake_store = FakeVectorStore(
        status=VectorIndexStatus(status="ready"),
        value_hits=[
            VectorSearchHit(
                "value",
                "dim_regions.region_group:华东",
                "华东",
                0.0,
                0.5,
                {"table_name": "dim_regions", "column_name": "region_group", "value": "华东"},
            ),
            VectorSearchHit(
                "value",
                "dim_channels.channel_name:天猫",
                "天猫",
                0.0,
                0.5,
                {"table_name": "dim_channels", "column_name": "channel_name", "value": "天猫"},
            ),
        ],
        hits={
            "value_vectors": [
                VectorSearchHit(
                    "value",
                    "dim_regions.region_group:华东",
                    "华东",
                    0.1,
                    0.9,
                    {"table_name": "dim_regions", "column_name": "region_group", "value": "华东"},
                )
            ]
        },
    )
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(threshold=0.7))
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)

    hits = searcher.search_values("华东地区天猫渠道销售额", query_vector=[0.1, 0.2, 0.3])

    assert {
        (hit.table_name, hit.column_name, hit.matched_value, hit.source, hit.score)
        for hit in hits
    } == {
        ("dim_regions", "region_group", "华东", "exact", 1.0),
        ("dim_channels", "channel_name", "天猫", "exact", 1.0),
    }
    assert fake_store.list_value_calls == [_datasource_filter()]
    assert fake_store.search_calls == [("value_vectors", [0.1, 0.2, 0.3], 20, _datasource_filter())]


def test_search_values_uses_default_embedding_model_when_config_blank(monkeypatch):
    fake_store = FakeVectorStore(
        status=VectorIndexStatus(status="ready"),
        value_hits=[
            VectorSearchHit(
                "value",
                "dim_regions.region_group:华东",
                "华东",
                0.0,
                0.5,
                {"table_name": "dim_regions", "column_name": "region_group", "value": "华东"},
            )
        ],
    )
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(vector_enabled="auto", embedding_model=None))
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)

    hits = searcher.search_values("华东地区销售额", query_vector=[0.1, 0.2, 0.3])

    assert [(hit.table_name, hit.column_name, hit.matched_value, hit.source) for hit in hits] == [
        ("dim_regions", "region_group", "华东", "exact")
    ]


def test_search_values_returns_empty_when_qdrant_unavailable(monkeypatch):
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(vector_enabled="auto"))
    monkeypatch.setattr(searcher, "get_vector_store", lambda: (_ for _ in ()).throw(RuntimeError("Qdrant down")))

    assert searcher.search_values("华东地区销售额") == []


def test_search_values_keeps_exact_hits_when_embedding_fails(monkeypatch):
    fake_store = FakeVectorStore(
        status=VectorIndexStatus(status="ready"),
        value_hits=[
            VectorSearchHit(
                "value",
                "dim_regions.region_group:华东",
                "华东",
                0.0,
                0.5,
                {"table_name": "dim_regions", "column_name": "region_group", "value": "华东"},
            )
        ],
    )
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(vector_enabled="auto"))
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)
    monkeypatch.setattr(searcher, "embed_text", lambda question: (_ for _ in ()).throw(RuntimeError("no model")))

    hits = searcher.search_values("华东地区销售额")

    assert [(hit.table_name, hit.column_name, hit.matched_value, hit.source) for hit in hits] == [
        ("dim_regions", "region_group", "华东", "exact")
    ]
    assert fake_store.search_calls == []


def test_search_values_ignores_numeric_exact_matches(monkeypatch):
    fake_store = FakeVectorStore(
        status=VectorIndexStatus(status="ready"),
        value_hits=[
            VectorSearchHit(
                "value",
                "dim_date.month:3",
                "3",
                0.0,
                0.5,
                {"table_name": "dim_date", "column_name": "month", "value": "3"},
            ),
            VectorSearchHit(
                "value",
                "fact_orders.channel_key:3",
                "3",
                0.0,
                0.5,
                {"table_name": "fact_orders", "column_name": "channel_key", "value": "3"},
            ),
            VectorSearchHit(
                "value",
                "dim_date.week:30",
                "30",
                0.0,
                0.5,
                {"table_name": "dim_date", "column_name": "week", "value": "30"},
            ),
            VectorSearchHit(
                "value",
                "dim_regions.region_group:华东",
                "华东",
                0.0,
                0.5,
                {"table_name": "dim_regions", "column_name": "region_group", "value": "华东"},
            ),
        ],
    )
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(threshold=0.7))
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)

    hits = searcher.search_values("最近30天华东销售额", query_vector=[0.1, 0.2, 0.3])

    assert [(hit.table_name, hit.column_name, hit.matched_value, hit.source) for hit in hits] == [
        ("dim_regions", "region_group", "华东", "exact")
    ]
    assert fake_store.list_value_calls == [_datasource_filter()]


def test_search_values_ignores_numeric_vector_matches(monkeypatch):
    fake_store = FakeVectorStore(
        status=VectorIndexStatus(status="ready"),
        hits={
            "value_vectors": [
                VectorSearchHit(
                    "value",
                    "dim_date.month:3",
                    "3",
                    0.01,
                    0.99,
                    {"table_name": "dim_date", "column_name": "month", "value": "3"},
                ),
                VectorSearchHit(
                    "value",
                    "dim_products.category:美妆个护",
                    "美妆个护",
                    0.1,
                    0.9,
                    {"table_name": "dim_products", "column_name": "category", "value": "美妆个护"},
                ),
            ]
        },
    )
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(threshold=0.7))
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)

    hits = searcher.search_values("彩妆护肤销售额", query_vector=[0.1, 0.2, 0.3])

    assert [(hit.table_name, hit.column_name, hit.matched_value, hit.source, hit.score) for hit in hits] == [
        ("dim_products", "category", "美妆个护", "vector", 0.9)
    ]


def test_search_values_embeds_question_for_vector_value_search(monkeypatch):
    fake_store = FakeVectorStore(
        status=VectorIndexStatus(status="ready"),
        hits={
            "value_vectors": [
                VectorSearchHit(
                    "value",
                    "dim_products.category:美妆个护",
                    "美妆个护",
                    0.1,
                    0.9,
                    {"table_name": "dim_products", "column_name": "category", "value": "美妆个护"},
                )
            ]
        },
    )
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings(threshold=0.7))
    monkeypatch.setattr(searcher, "get_vector_store", lambda: fake_store)
    monkeypatch.setattr(searcher, "embed_text", lambda question: [0.1, 0.2, 0.3])

    hits = searcher.search_values("彩妆护肤销售额")

    assert [(hit.table_name, hit.column_name, hit.matched_value, hit.source, hit.score) for hit in hits] == [
        ("dim_products", "category", "美妆个护", "vector", 0.9)
    ]
    assert fake_store.search_calls == [("value_vectors", [0.1, 0.2, 0.3], 20, _datasource_filter())]


def _settings(vector_enabled=True, embedding_model="local/custom-embedding-model", threshold=0.7):
    return SimpleNamespace(
        vector_enabled=vector_enabled,
        embedding_model=embedding_model,
        vector_similarity_threshold=threshold,
        value_vector_similarity_threshold=threshold,
    )


class FakeVectorStore:
    def __init__(self, status, hits=None, value_hits=None) -> None:
        self._status = status
        self._hits = hits or {}
        self._value_hits = value_hits or []
        self.search_calls = []
        self.list_value_calls = []

    def status(self, expected_model=None, expected_dimension=None):
        self.expected_model = expected_model
        self.expected_dimension = expected_dimension
        return self._status

    def search(self, table_name, vector, limit=10, where=None):
        self.search_calls.append((table_name, vector, limit, where))
        return self._hits.get(table_name, [])

    def list_values(self, where=None):
        self.list_value_calls.append(where)
        return self._value_hits


def _datasource_filter(datasource_name: str = DEFAULT_DATASOURCE) -> dict:
    return {"must": [{"key": "metadata.datasource", "match": {"value": datasource_name}}]}
