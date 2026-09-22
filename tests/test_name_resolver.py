"""Unit tests for tools/name_resolver.py resolve() — five-level matching, fully offline."""

import pytest

import tools.name_resolver as name_resolver

DEFAULT_STOCKS = [
    {"code": "600519", "name": "贵州茅台"},
    {"code": "600520", "name": "文一科技"},
    {"code": "000001", "name": "平安银行"},
    {"code": "300750", "name": "宁德时代"},
    {"code": "002594", "name": "比亚迪"},
]


@pytest.fixture
def set_stocks(monkeypatch):
    def _set(stocks):
        monkeypatch.setattr(name_resolver, "_load_stock_map", lambda: stocks)

    return _set


class TestExactMatch:
    def test_exact_code(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("600519") == [{"code": "600519", "name": "贵州茅台"}]

    def test_exact_name(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("贵州茅台") == [{"code": "600519", "name": "贵州茅台"}]

    def test_exact_result_carries_no_score(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        result = name_resolver.resolve("比亚迪")
        assert result == [{"code": "002594", "name": "比亚迪"}]

    def test_surrounding_whitespace_is_stripped(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("  600519  ") == [{"code": "600519", "name": "贵州茅台"}]


class TestPrefixCode:
    def test_code_prefix_matches_all(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("6005") == [
            {"code": "600519", "name": "贵州茅台"},
            {"code": "600520", "name": "文一科技"},
        ]

    def test_prefix_respects_top(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("6005", top=1) == [{"code": "600519", "name": "贵州茅台"}]


class TestContains:
    def test_name_substring(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("平安") == [{"code": "000001", "name": "平安银行"}]

    def test_contains_short_circuits_fuzzy(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        result = name_resolver.resolve("宁德")
        assert result == [{"code": "300750", "name": "宁德时代"}]
        assert "score" not in result[0]


class TestPinyinMatch:
    def test_full_pinyin_substring_scores_0_85(self, set_stocks):
        pytest.importorskip("pypinyin")
        set_stocks(DEFAULT_STOCKS)
        result = name_resolver.resolve("maotai")
        assert result[0]["code"] == "600519"
        assert result[0]["score"] == 0.85

    def test_full_pinyin_exact_scores_0_95(self, set_stocks):
        pytest.importorskip("pypinyin")
        set_stocks(DEFAULT_STOCKS)
        result = name_resolver.resolve("guizhoumaotai")
        assert result[0]["code"] == "600519"
        assert result[0]["score"] == 0.95

    def test_pinyin_initials_match(self, set_stocks):
        pytest.importorskip("pypinyin")
        set_stocks(DEFAULT_STOCKS)
        result = name_resolver.resolve("gzmt")
        assert result[0]["code"] == "600519"
        assert result[0]["score"] == 0.75

    def test_exact_pinyin_initials_score_highest(self, set_stocks):
        pytest.importorskip("pypinyin")
        set_stocks([{"code": "300999", "name": "拼多多"}])
        # 胖嘟嘟 and 拼多多 share the pinyin initials "pdd" without any substring overlap
        result = name_resolver.resolve("胖嘟嘟")
        assert result[0] == {"code": "300999", "name": "拼多多", "score": 0.9}


class TestFuzzyScoring:
    def test_typo_name_still_matches(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        result = name_resolver.resolve("贵州茅苔")  # 苔/台 one-char typo
        assert result[0]["code"] == "600519"
        assert result[0]["score"] > 0.4

    def test_case_insensitive_ascii_name(self, set_stocks):
        set_stocks([{"code": "999999", "name": "TESTABC"}])
        result = name_resolver.resolve("testabd")
        assert result[0]["code"] == "999999"
        assert result[0]["score"] == 0.86

    def test_results_sorted_by_score_descending(self, set_stocks):
        set_stocks(DEFAULT_STOCKS + [{"code": "600600", "name": "贵州燃气"}])
        result = name_resolver.resolve("贵州茅台x")
        assert result[0]["code"] == "600519"
        scores = [r["score"] for r in result]
        assert len(scores) >= 2
        assert scores == sorted(scores, reverse=True)

    def test_below_threshold_returns_empty(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("xxxx") == []


class TestEdgeCases:
    def test_empty_query_is_prefix_of_everything(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("", top=3) == DEFAULT_STOCKS[:3]

    def test_whitespace_only_query(self, set_stocks):
        set_stocks(DEFAULT_STOCKS)
        assert name_resolver.resolve("   ") == DEFAULT_STOCKS[:5]

    def test_empty_stock_map(self, set_stocks):
        set_stocks([])
        assert name_resolver.resolve("600519") == []
