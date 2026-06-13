from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.app.config import PROJECT_ROOT

_DEFAULT_PATH = PROJECT_ROOT / "evals" / "promoted_patterns.json"
_CACHE: dict[Path, tuple[int | None, frozenset[str]]] = {}
_CACHE_LOCK = threading.Lock()


def load_promoted_patterns(*, path: Path | None = None) -> frozenset[str]:
    resolved = Path(path) if path is not None else _DEFAULT_PATH
    signature = _artifact_signature(resolved)
    with _CACHE_LOCK:
        cached = _CACHE.get(resolved)
        if cached is not None and cached[0] == signature:
            return cached[1]
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        patterns = frozenset()
        with _CACHE_LOCK:
            _CACHE[resolved] = (signature, patterns)
        return patterns
    promoted = data.get("promoted") if isinstance(data, dict) else None
    patterns = frozenset(str(pattern) for pattern in promoted) if isinstance(promoted, list) else frozenset()
    with _CACHE_LOCK:
        _CACHE[resolved] = (signature, patterns)
    return patterns


def is_pattern_promoted(pattern: str | None, *, path: Path | None = None) -> bool:
    return bool(pattern) and pattern in load_promoted_patterns(path=path)


def _artifact_signature(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None
