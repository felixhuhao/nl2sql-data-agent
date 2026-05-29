from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.metadata.service import build_explainability_context


if __name__ == "__main__":
    print(json.dumps(build_explainability_context(), ensure_ascii=False, indent=2))
