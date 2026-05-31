from types import SimpleNamespace

import pytest

from backend.app.metadata.vector import embedding


class FakeEmbeddingModel:
    def __init__(self) -> None:
        self.calls = []

    def encode(self, texts, normalize_embeddings=True):
        self.calls.append((texts, normalize_embeddings))
        if isinstance(texts, str):
            return [1, 2, 3]
        return [[index + 1, index + 2, index + 3] for index, _ in enumerate(texts)]


@pytest.fixture(autouse=True)
def clear_embedding_cache():
    embedding.clear_embedding_model_cache()
    yield
    embedding.clear_embedding_model_cache()


def test_embed_text_returns_none_for_blank_without_loading(monkeypatch):
    _patch_settings(monkeypatch)
    monkeypatch.setattr(
        embedding,
        "_load_sentence_transformer",
        lambda model_name: pytest.fail("blank text should not load embedding model"),
    )

    assert embedding.embed_text("   ") is None


def test_embed_text_returns_float_vector(monkeypatch):
    model = FakeEmbeddingModel()
    _patch_settings(monkeypatch)
    monkeypatch.setattr(embedding, "_load_sentence_transformer", lambda model_name: model)

    assert embedding.embed_text(" 销售额 ") == [1.0, 2.0, 3.0]
    assert model.calls == [("销售额", True)]


def test_embed_texts_preserves_empty_positions(monkeypatch):
    model = FakeEmbeddingModel()
    _patch_settings(monkeypatch)
    monkeypatch.setattr(embedding, "_load_sentence_transformer", lambda model_name: model)

    assert embedding.embed_texts(["销售额", "", "订单数"]) == [
        [1.0, 2.0, 3.0],
        None,
        [2.0, 3.0, 4.0],
    ]
    assert model.calls == [(["销售额", "订单数"], True)]


def test_get_embedding_dimension_uses_config_without_loading(monkeypatch):
    _patch_settings(monkeypatch, enabled=False, dimension=512)
    monkeypatch.setattr(
        embedding,
        "_load_sentence_transformer",
        lambda model_name: pytest.fail("configured dimension should not load embedding model"),
    )

    assert embedding.get_embedding_dimension() == 512


def test_get_embedding_dimension_infers_from_model(monkeypatch):
    model = FakeEmbeddingModel()
    _patch_settings(monkeypatch)
    monkeypatch.setattr(embedding, "_load_sentence_transformer", lambda model_name: model)

    assert embedding.get_embedding_dimension() == 3
    assert model.calls == [(embedding.DEFAULT_DIMENSION_PROBE_TEXT, True)]


def test_get_embedding_model_raises_when_vector_disabled_without_loading(monkeypatch):
    _patch_settings(monkeypatch, enabled=False)
    monkeypatch.setattr(
        embedding,
        "_load_sentence_transformer",
        lambda model_name: pytest.fail("disabled vector search should not load embedding model"),
    )

    with pytest.raises(RuntimeError, match="Vector search is disabled"):
        embedding.get_embedding_model()


def test_get_embedding_model_requires_model_when_vector_enabled(monkeypatch):
    _patch_settings(monkeypatch, model_name=None)
    monkeypatch.setattr(
        embedding,
        "_load_sentence_transformer",
        lambda model_name: pytest.fail("missing model should not call loader"),
    )

    with pytest.raises(RuntimeError, match="EMBEDDING_MODEL"):
        embedding.get_embedding_model()


def test_get_embedding_model_caches_by_model_name(monkeypatch):
    loaded_models = []
    _patch_settings(monkeypatch, model_name="fake-model")

    def load_model(model_name):
        loaded_models.append(model_name)
        return FakeEmbeddingModel()

    monkeypatch.setattr(embedding, "_load_sentence_transformer", load_model)

    assert embedding.get_embedding_model() is embedding.get_embedding_model()
    assert loaded_models == ["fake-model"]


def _patch_settings(
    monkeypatch,
    *,
    enabled: bool = True,
    model_name: str | None = "fake-model",
    dimension: int | None = None,
) -> None:
    monkeypatch.setattr(
        embedding,
        "get_settings",
        lambda: SimpleNamespace(
            vector_enabled=enabled,
            embedding_model=model_name,
            embedding_dimension=dimension,
        ),
    )
