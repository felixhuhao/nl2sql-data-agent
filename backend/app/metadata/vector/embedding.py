from functools import lru_cache
from typing import Sequence

from backend.app.config import get_settings


DEFAULT_DIMENSION_PROBE_TEXT = "销售额"


def embed_text(text: str) -> list[float] | None:
    normalized = _normalize_text(text)
    if normalized is None:
        return None

    model = get_embedding_model()
    vector = model.encode(normalized, normalize_embeddings=True)
    return _coerce_vector(vector)


def embed_texts(texts: Sequence[str]) -> list[list[float] | None]:
    normalized_texts = [_normalize_text(text) for text in texts]
    results: list[list[float] | None] = [None] * len(normalized_texts)
    non_empty = [(index, text) for index, text in enumerate(normalized_texts) if text is not None]
    if not non_empty:
        return results

    model = get_embedding_model()
    vectors = model.encode([text for _, text in non_empty], normalize_embeddings=True)
    for (index, _), vector in zip(non_empty, _coerce_vectors(vectors), strict=True):
        results[index] = vector
    return results


def get_embedding_dimension() -> int:
    settings = get_settings()
    if settings.embedding_dimension is not None:
        return settings.embedding_dimension

    vector = embed_text(DEFAULT_DIMENSION_PROBE_TEXT)
    if vector is None:
        raise RuntimeError("Could not infer embedding dimension.")
    return len(vector)


def get_embedding_model():
    settings = get_settings()
    if not settings.vector_enabled:
        raise RuntimeError("Vector search is disabled. Set VECTOR_ENABLED=true to load embeddings.")
    if not settings.embedding_model:
        raise RuntimeError("EMBEDDING_MODEL is required when VECTOR_ENABLED=true.")
    return _cached_embedding_model(settings.embedding_model)


def clear_embedding_model_cache() -> None:
    _cached_embedding_model.cache_clear()


@lru_cache(maxsize=4)
def _cached_embedding_model(model_name: str):
    return _load_sentence_transformer(model_name)


def _load_sentence_transformer(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for vector search. "
            "Install backend dependencies or set VECTOR_ENABLED=false."
        ) from exc

    return SentenceTransformer(model_name)


def _normalize_text(text: str) -> str | None:
    normalized = text.strip()
    return normalized or None


def _coerce_vector(vector) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]


def _coerce_vectors(vectors) -> list[list[float]]:
    if hasattr(vectors, "tolist"):
        vectors = vectors.tolist()
    if vectors and isinstance(vectors[0], int | float):
        return [_coerce_vector(vectors)]
    return [_coerce_vector(vector) for vector in vectors]
