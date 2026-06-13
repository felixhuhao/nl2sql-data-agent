from backend.app.core.date_rules import build_relative_date_rules, match_relative_date_rule


def test_build_relative_date_rules_uses_dataset_current_date():
    rules = build_relative_date_rules("2026-06-11")

    assert rules["最近7天"] == {"start": "2026-06-05", "end": "2026-06-11"}
    assert rules["最近30天"] == {"start": "2026-05-13", "end": "2026-06-11"}
    assert rules["本月"] == {"start": "2026-06-01", "end": "2026-06-11"}
    assert rules["last 30 days"] == rules["最近30天"]


def test_match_relative_date_rule_supports_common_aliases():
    assert match_relative_date_rule("show revenue for last 7 days", "2026-06-11") == {
        "phrase": "last 7 days",
        "start": "2026-06-05",
        "end": "2026-06-11",
    }
    assert match_relative_date_rule("本月销售额", "2026-06-11") == {
        "phrase": "本月",
        "start": "2026-06-01",
        "end": "2026-06-11",
    }
    assert match_relative_date_rule("all-time revenue", "2026-06-11") is None


def test_match_relative_date_rule_avoids_loose_substring_matches():
    assert match_relative_date_rule("show revenue for blast 7 days", "2026-06-11") is None
    assert match_relative_date_rule("本月度指标", "2026-06-11") is None
    assert match_relative_date_rule("查询最近 30 天销售额", "2026-06-11") == {
        "phrase": "最近30天",
        "start": "2026-05-13",
        "end": "2026-06-11",
    }
