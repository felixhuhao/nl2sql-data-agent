import os

from backend.app.agent.promoted_patterns import is_pattern_promoted, load_promoted_patterns


def test_load_promoted_patterns_reads_artifact(tmp_path):
    path = tmp_path / "promoted_patterns.json"
    path.write_text('{"promoted": ["concept_absent_full_metadata"]}', encoding="utf-8")

    assert load_promoted_patterns(path=path) == frozenset({"concept_absent_full_metadata"})


def test_is_pattern_promoted_defaults_false_when_missing(tmp_path):
    assert is_pattern_promoted("anything", path=tmp_path / "missing.json") is False


def test_load_promoted_patterns_reloads_when_artifact_changes(tmp_path):
    path = tmp_path / "promoted_patterns.json"
    path.write_text('{"promoted": ["concept_absent_full_metadata"]}', encoding="utf-8")
    first_mtime = path.stat().st_mtime_ns

    assert load_promoted_patterns(path=path) == frozenset({"concept_absent_full_metadata"})

    path.write_text('{"promoted": ["value_absent_distinct_probe"]}', encoding="utf-8")
    if path.stat().st_mtime_ns == first_mtime:
        os.utime(path, ns=(first_mtime + 1, first_mtime + 1))

    assert load_promoted_patterns(path=path) == frozenset({"value_absent_distinct_probe"})
