from types import SimpleNamespace

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


def test_retrieve_vector_assets_returns_stale_status(monkeypatch):
    fake_store = FakeVectorStore(status=VectorIndexStatus(status="stale", stale_reason="old model"))
    monkeypatch.setattr(searcher, "get_settings", lambda: _settings())
    monkeypatch.setattr(searcher, "get_embedding_dimension", lambda: 3)
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
    monkeypatch.setattr(searcher, "get_embedding_dimension", lambda: 3)
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


def _settings(vector_enabled=True, embedding_model="D:/Models/BAAI/bge-m3", threshold=0.7):
    return SimpleNamespace(
        vector_enabled=vector_enabled,
        embedding_model=embedding_model,
        vector_similarity_threshold=threshold,
    )


class FakeVectorStore:
    def __init__(self, status, hits=None) -> None:
        self._status = status
        self._hits = hits or {}
        self.search_calls = []

    def status(self, expected_model=None, expected_dimension=None):
        self.expected_model = expected_model
        self.expected_dimension = expected_dimension
        return self._status

    def search(self, table_name, vector, limit=10):
        self.search_calls.append((table_name, vector, limit))
        return self._hits.get(table_name, [])
