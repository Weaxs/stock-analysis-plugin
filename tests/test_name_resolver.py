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
        monkeypatch.setattr(name_resolver, "_search", lambda query, count: stocks)

    return _set


def _search_response(monkeypatch, rows, code="0"):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": code, "msg": "Success", "result": rows}

    def fake_get(url, params, headers, timeout):
        calls.append(params)
        return Response()

    monkeypatch.setattr("requests.get", fake_get)
    return calls


class TestSearch:
    def test_keeps_only_listed_a_hk_us_stocks(self, monkeypatch):
        rows = [
            {"code": "600519", "shortName": "贵州茅台", "market": 1, "securityTypeName": "沪A"},
            {"code": "688981", "shortName": "中芯国际", "market": 1, "securityTypeName": "科创板"},
            {"code": "00981", "shortName": "中芯国际", "market": 116, "securityTypeName": "港股"},
            {"code": "BRK_B", "shortName": "伯克希尔-B", "market": 106, "securityTypeName": "美股"},
            {"code": "000847", "shortName": "腾讯济安", "market": 1, "securityTypeName": "指数"},
            {"code": "BK1189", "shortName": "腾讯概念", "market": 90, "securityTypeName": "概念"},
            {"code": "APC", "shortName": "苹果", "market": 185, "securityTypeName": "德国"},
            {"code": "832687", "shortName": "京东农业", "market": 0, "securityTypeName": "三板"},
            {"code": "ODD$", "shortName": "异常", "market": 105, "securityTypeName": "美股"},
            {"code": "600000", "shortName": "", "market": 1, "securityTypeName": "沪A"},
        ]
        calls = _search_response(monkeypatch, rows)
        assert name_resolver._search("中芯", 20) == [
            {"code": "600519", "name": "贵州茅台"},
            {"code": "688981", "name": "中芯国际"},
            {"code": "00981.HK", "name": "中芯国际"},
            {"code": "BRK-B", "name": "伯克希尔-B"},
        ]
        assert calls[0]["keyword"] == "中芯" and calls[0]["pageSize"] == 20

    def test_provider_error_raises_runtime_error(self, monkeypatch):
        _search_response(monkeypatch, None, code="400")
        with pytest.raises(RuntimeError, match="stock search unavailable"):
            name_resolver.resolve("贵州茅台")

    def test_one_request_per_query(self, monkeypatch):
        calls = _search_response(
            monkeypatch, [{"code": "AAPL", "shortName": "苹果", "market": 105, "securityTypeName": "美股"}]
        )
        assert name_resolver.resolve("aapl") == [{"code": "AAPL", "name": "苹果"}]
        assert len(calls) == 1


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
    def test_blank_query_skips_the_provider(self, monkeypatch):
        monkeypatch.setattr(name_resolver, "_search", lambda *a: pytest.fail("blank query reached provider"))
        assert name_resolver.resolve("   ") == []

    def test_empty_search_result(self, set_stocks):
        set_stocks([])
        assert name_resolver.resolve("600519") == []
