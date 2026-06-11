from types import SimpleNamespace

from backend.app.config import DEFAULT_EMBEDDING_MODEL
from backend.app.metadata.vector import admin
from backend.app.metadata.vector.store import VectorIndexStatus


def test_vector_status_auto_uses_default_embedding_model_without_config(monkeypatch):
    fake_store = FakeVectorStore(VectorIndexStatus(status="missing", stale_reason="Missing index metadata."))
    monkeypatch.setattr(admin, "get_settings", lambda: _settings(embedding_model=None))
    monkeypatch.setattr(admin, "get_vector_store", lambda: fake_store)

    status = admin.get_vector_index_status()

    assert status["vector_enabled"] is True
    assert status["vector_mode"] == "auto"
    assert status["status"] == "missing"
    assert status["embedding_model"] == DEFAULT_EMBEDDING_MODEL
    assert status["stale_reason"] == "Missing index metadata."
    assert fake_store.expected_model == DEFAULT_EMBEDDING_MODEL
    assert fake_store.expected_dimension is None


def test_vector_status_auto_falls_back_to_disabled_when_qdrant_unavailable(monkeypatch):
    monkeypatch.setattr(admin, "get_settings", lambda: _settings())
    monkeypatch.setattr(admin, "get_vector_store", lambda: (_ for _ in ()).throw(RuntimeError("Qdrant down")))

    status = admin.get_vector_index_status()

    assert status["vector_enabled"] is False
    assert status["vector_mode"] == "auto"
    assert status["status"] == "disabled"
    assert status["stale_reason"] == "Vector index is unavailable: Qdrant down"


def test_vector_status_auto_reports_ready_index(monkeypatch):
    monkeypatch.setattr(admin, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        admin,
        "get_vector_store",
        lambda: FakeVectorStore(VectorIndexStatus(status="ready", asset_counts={"metric": 2})),
    )

    status = admin.get_vector_index_status()

    assert status["vector_enabled"] is True
    assert status["vector_mode"] == "auto"
    assert status["status"] == "ready"
    assert status["asset_counts"] == {"metric": 2}


def _settings(vector_enabled="auto", embedding_model="fake-model"):
    return SimpleNamespace(
        vector_enabled=vector_enabled,
        embedding_model=embedding_model,
        embedding_dimension=None,
        qdrant_url="http://qdrant:6333",
        qdrant_collection_prefix="nl2sql",
    )


class FakeVectorStore:
    def __init__(self, status: VectorIndexStatus) -> None:
        self._status = status

    def status(self, expected_model=None, expected_dimension=None):
        self.expected_model = expected_model
        self.expected_dimension = expected_dimension
        return self._status
