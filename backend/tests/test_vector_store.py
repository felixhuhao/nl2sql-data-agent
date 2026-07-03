from types import SimpleNamespace

import pytest

from backend.app.metadata.vector import store


def test_ensure_tables_creates_all_vector_collections():
    client = FakeQdrantClient()
    vector_store = store.QdrantVectorStore(client=client, collection_prefix="test")

    vector_store.ensure_tables(embedding_dimension=3)

    assert set(client.collections) == {
        "test_table_vectors",
        "test_column_vectors",
        "test_metric_vectors",
        "test_verified_query_vectors",
        "test_value_vectors",
        "test__index_metadata",
    }
    assert client.collections["test_metric_vectors"].vectors_config == {"size": 3, "distance": "Cosine"}
    assert client.collections["test__index_metadata"].vectors_config == {"size": 1, "distance": "Cosine"}


def test_upsert_search_list_values_and_delete():
    client = FakeQdrantClient.with_collections(["test_metric_vectors", "test_value_vectors"])
    vector_store = store.QdrantVectorStore(client=client, collection_prefix="test")

    vector_store.upsert_rows(
        [
            store.VectorRow(
                table_name="metric_vectors",
                asset_type="metric",
                asset_id="sales_amount",
                text="销售额",
                vector=[0.1, 0.2, 0.3],
                metadata={"label": "销售额"},
            )
        ]
    )

    hits = vector_store.search("metric_vectors", [0.1, 0.2, 0.3], limit=1, where="asset_type = 'metric'")
    assert hits == [
        store.VectorSearchHit(
            asset_type="metric",
            asset_id="sales_amount",
            text="销售额",
            distance=0.2,
            score=0.8,
            metadata={"label": "销售额"},
        )
    ]
    assert client.search_calls[0]["query_filter"] == {
        "must": [{"key": "asset_type", "match": {"value": "metric"}}]
    }

    values = vector_store.list_values(limit=5, where="asset_type = 'value'")
    assert values == []
    assert client.scroll_calls[0]["collection_name"] == "test_value_vectors"
    assert client.scroll_calls[0]["limit"] == 5
    assert client.scroll_calls[0]["scroll_filter"] == {
        "must": [{"key": "asset_type", "match": {"value": "value"}}]
    }

    vector_store.delete_by_ids("metric_vectors", ["metric:sales_amount", "metric:aov's"])
    assert client.collections["test_metric_vectors"].deleted_selectors == [
        {
            "points": [
                store._point_id_for_row("metric:sales_amount"),
                store._point_id_for_row("metric:aov's"),
            ]
        }
    ]


def test_clear_vector_tables_deletes_only_vector_collections():
    client = FakeQdrantClient.with_collections(f"test_{name}" for name in store.ALL_TABLE_NAMES)
    vector_store = store.QdrantVectorStore(client=client, collection_prefix="test")

    vector_store.clear_vector_tables()

    for table_name in store.VECTOR_TABLE_NAMES:
        assert client.collections[f"test_{table_name}"].deleted_selectors == [{"filter": {}}]
    assert client.collections["test__index_metadata"].deleted_selectors == []


def test_metadata_round_trip_and_ready_status():
    client = FakeQdrantClient.with_collections(f"test_{name}" for name in store.ALL_TABLE_NAMES)
    vector_store = store.QdrantVectorStore(client=client, collection_prefix="test")
    metadata = store.VectorIndexMetadata(
        embedding_model="/models/custom-embedding-model",
        embedding_dimension=768,
        built_at="2026-05-31T10:00:00Z",
        asset_counts={"metric": 3},
    )

    vector_store.write_metadata(metadata)

    assert vector_store.read_metadata() == metadata
    assert vector_store.status(
        expected_model="/models/custom-embedding-model",
        expected_dimension=768,
    ) == store.VectorIndexStatus(
        status="ready",
        embedding_model="/models/custom-embedding-model",
        embedding_dimension=768,
        built_at="2026-05-31T10:00:00Z",
        asset_counts={"metric": 3},
    )


def test_status_reports_missing_and_stale():
    client = FakeQdrantClient.with_collections(f"test_{name}" for name in store.ALL_TABLE_NAMES)
    vector_store = store.QdrantVectorStore(client=client, collection_prefix="test")

    assert vector_store.status().status == "missing"

    vector_store.write_metadata(
        store.VectorIndexMetadata(
            embedding_model="model-a",
            embedding_dimension=3,
            built_at="2026-05-31T10:00:00Z",
            asset_counts={},
        )
    )

    stale_status = vector_store.status(expected_model="model-b", expected_dimension=3)
    assert stale_status.status == "stale"
    assert stale_status.stale_reason == "Embedding model mismatch."

    missing_store = store.QdrantVectorStore(client=FakeQdrantClient(), collection_prefix="test")
    assert missing_store.status().status == "missing"
    assert "Missing Qdrant collections" in (missing_store.status().stale_reason or "")


def test_mark_stale_persists_stale_reason():
    client = FakeQdrantClient.with_collections(f"test_{name}" for name in store.ALL_TABLE_NAMES)
    vector_store = store.QdrantVectorStore(client=client, collection_prefix="test")
    vector_store.write_metadata(
        store.VectorIndexMetadata(
            embedding_model="model-a",
            embedding_dimension=3,
            built_at="2026-05-31T10:00:00Z",
            asset_counts={"metric": 3},
        )
    )

    vector_store.mark_stale("Metric changed.")

    status = vector_store.status(expected_model="model-a", expected_dimension=3)
    assert status.status == "stale"
    assert status.stale_reason == "Metric changed."


def test_invalid_table_name_is_rejected():
    vector_store = store.QdrantVectorStore(client=FakeQdrantClient(), collection_prefix="test")

    with pytest.raises(ValueError, match="Unknown vector table"):
        vector_store.search("bad_table", [1.0])


def test_qdrant_client_uses_configured_timeout(monkeypatch):
    fake_module = SimpleNamespace(QdrantClient=FakeQdrantClientFactory())
    monkeypatch.setattr(store, "_load_qdrant_client", lambda: fake_module)

    vector_store = store.QdrantVectorStore(
        url="http://qdrant:6333",
        api_key="test-key",
        collection_prefix="test",
        timeout=90,
    )

    assert vector_store._client_instance() is fake_module.QdrantClient.client
    assert fake_module.QdrantClient.kwargs == {
        "url": "http://qdrant:6333",
        "api_key": "test-key",
        "timeout": 90,
    }


class FakeQdrantClientFactory:
    def __init__(self) -> None:
        self.client = FakeQdrantClient()
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.client


class FakeQdrantClient:
    def __init__(self) -> None:
        self.collections = {}
        self.search_calls = []
        self.scroll_calls = []

    @classmethod
    def with_collections(cls, collection_names) -> "FakeQdrantClient":
        client = cls()
        for collection_name in collection_names:
            client.collections[collection_name] = FakeCollection(collection_name)
        return client

    def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=name) for name in self.collections]
        )

    def create_collection(self, collection_name, vectors_config):
        self.collections[collection_name] = FakeCollection(
            collection_name,
            vectors_config=vectors_config,
        )

    def upsert(self, collection_name, points, wait=True):
        collection = self.collections[collection_name]
        for point in points:
            collection.points[point["id"]] = point

    def delete(self, collection_name, points_selector, wait=True):
        collection = self.collections[collection_name]
        collection.deleted_selectors.append(points_selector)
        if "points" in points_selector:
            for point_id in points_selector["points"]:
                collection.points.pop(point_id, None)
        elif "filter" in points_selector:
            collection.points.clear()

    def search(self, collection_name, query_vector, query_filter=None, limit=10, with_payload=True):
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "query_vector": query_vector,
                "query_filter": query_filter,
                "limit": limit,
            }
        )
        records = [
            point
            for point in self.collections[collection_name].points.values()
            if _matches_filter(point, query_filter)
        ][:limit]
        return [FakePoint(record["payload"], score=0.8) for record in records]

    def scroll(self, collection_name, limit=10, scroll_filter=None, with_payload=True, with_vectors=False):
        self.scroll_calls.append({"collection_name": collection_name, "limit": limit, "scroll_filter": scroll_filter})
        records = [
            point
            for point in self.collections[collection_name].points.values()
            if _matches_filter(point, scroll_filter)
        ][:limit]
        return [FakePoint(record["payload"]) for record in records], None

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        collection = self.collections[collection_name]
        return [
            FakePoint(collection.points[point_id]["payload"])
            for point_id in ids
            if point_id in collection.points
        ]


class FakeCollection:
    def __init__(self, name, vectors_config=None) -> None:
        self.name = name
        self.vectors_config = vectors_config
        self.points = {}
        self.deleted_selectors = []


class FakePoint:
    def __init__(self, payload, score=0.0) -> None:
        self.payload = payload
        self.score = score


def _matches_filter(point, query_filter):
    if not query_filter:
        return True
    payload = point["payload"]
    for condition in query_filter.get("must", []):
        key = condition["key"]
        expected = condition["match"]["value"]
        if payload.get(key) != expected:
            return False
    return True
