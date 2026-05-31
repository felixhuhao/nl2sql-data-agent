from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.metadata.vector.indexer import rebuild_vector_index


if __name__ == "__main__":
    result = rebuild_vector_index()
    print(
        {
            "embedding_model": result.embedding_model,
            "embedding_dimension": result.embedding_dimension,
            "built_at": result.built_at,
            "asset_counts": result.asset_counts,
        }
    )
