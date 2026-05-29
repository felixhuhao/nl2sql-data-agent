from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.metadata.sync import sync_metadata


if __name__ == "__main__":
    result = sync_metadata()
    print(result)
