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


@pytest.fixture(autouse=True)
def _fresh_international_map():
    name_resolver._load_international_stock_map.cache_clear()


@pytest.fixture
def set_stocks(monkeypatch):
    def _set(stocks):
        monkeypatch.setattr(name_resolver, "_load_stock_map", lambda: stocks)
        monkeypatch.setattr(name_resolver, "_load_international_stock_map", lambda: [], raising=False)

    return _set


class TestInternationalNames:
    def test_hk_chinese_name(self, set_stocks, monkeypatch):
        set_stocks(DEFAULT_STOCKS)
        monkeypatch.setattr(
            name_resolver, "_load_international_stock_map", lambda: [{"code": "00700.HK", "name": "腾讯控股"}]
        )
        assert name_resolver.resolve("腾讯") == [{"code": "00700.HK", "name": "腾讯控股"}]

    def test_us_provider_code_and_failed_hk_provider(self, monkeypatch, tmp_path):
        import sys
        from types import SimpleNamespace

        import pandas as pd

        def hk_unavailable():
            raise RuntimeError("HK provider down")

        akshare = SimpleNamespace(
            stock_zh_a_spot_em=lambda: pd.DataFrame([]),
            stock_hk_spot_em=hk_unavailable,
            stock_us_spot_em=lambda: pd.DataFrame([{"代码": "AAPL.OQ", "名称": "苹果"}]),
        )
        monkeypatch.setitem(sys.modules, "akshare", akshare)
        monkeypatch.setattr(name_resolver, "cached", lambda key, ttl, fetch: fetch())
        assert name_resolver.resolve("苹果") == [{"code": "AAPL", "name": "苹果"}]

    @pytest.mark.parametrize("query,expected", [("aapl", "AAPL"), ("00700.hk", "00700.HK")])
    def test_lowercase_international_code(self, set_stocks, monkeypatch, query, expected):
        set_stocks([])
        monkeypatch.setattr(
            name_resolver,
            "_load_international_stock_map",
            lambda: [{"code": "AAPL", "name": "苹果"}, {"code": "00700.HK", "name": "腾讯控股"}],
        )
        assert name_resolver.resolve(query) == [
            {"code": expected, "name": "苹果" if expected == "AAPL" else "腾讯控股"}
        ]

    def test_ascii_ticker_is_not_masked_by_local_pinyin_score(self, set_stocks, monkeypatch):
        set_stocks([{"code": "000001", "name": "平安银行"}])
        monkeypatch.setattr(name_resolver, "_load_international_stock_map", lambda: [{"code": "A", "name": "安捷伦"}])
        assert name_resolver.resolve("A") == [{"code": "A", "name": "安捷伦"}]

    @pytest.mark.parametrize(
        "raw_code,name,expected",
        [
            ("105.AAPL", "苹果", "AAPL"),
            ("105.AMZN", "亚马逊", "AMZN"),
            ("106.BA", "波音", "BA"),
            ("BRK.B.N", "伯克希尔", "BRK-B"),
            ("106.BRK_A", "伯克希尔-A", "BRK-A"),
        ],
    )
    def test_eastmoney_us_symbol_shapes(self, monkeypatch, raw_code, name, expected):
        import sys
        from types import SimpleNamespace

        import pandas as pd

        akshare = SimpleNamespace(
            stock_zh_a_spot_em=lambda: pd.DataFrame([]),
            stock_hk_spot_em=lambda: pd.DataFrame([]),
            stock_us_spot_em=lambda: pd.DataFrame([{"代码": raw_code, "名称": name}]),
        )
        monkeypatch.setitem(sys.modules, "akshare", akshare)
        monkeypatch.setattr(name_resolver, "cached", lambda key, ttl, fetch: fetch())
        assert name_resolver.resolve(name) == [{"code": expected, "name": name}]

    def test_odd_provider_row_does_not_drop_the_market(self, monkeypatch):
        import sys
        from types import SimpleNamespace

        import pandas as pd

        rows = [{"代码": "105.AAPL", "名称": "苹果"}, {"代码": "106.ODD$", "名称": "异常代码"}]
        akshare = SimpleNamespace(
            stock_hk_spot_em=lambda: pd.DataFrame([{"代码": "00700", "名称": "腾讯控股"}, {"代码": "X", "名称": ""}]),
            stock_us_spot_em=lambda: pd.DataFrame(rows),
        )
        monkeypatch.setitem(sys.modules, "akshare", akshare)
        monkeypatch.setattr(name_resolver, "cached", lambda key, ttl, fetch: fetch())
        assert name_resolver._load_international_stock_map() == [
            {"code": "00700.HK", "name": "腾讯控股"},
            {"code": "AAPL", "name": "苹果"},
        ]

    def test_unavailable_markets_are_tried_once_per_process(self, monkeypatch):
        monkeypatch.setattr(name_resolver, "_load_stock_map", lambda: [{"code": "000725", "name": "京东方A"}])
        calls = []

        def down(key, ttl, fetch):
            calls.append(key)
            raise RuntimeError("rate limited")

        monkeypatch.setattr(name_resolver, "cached", down)
        name_resolver.resolve("京东")
        name_resolver.resolve("京东")
        assert calls == ["stock-name-map:hk:v1", "stock-name-map:us:v1"]

    def test_a_share_substring_hit_still_offers_hk_names(self, set_stocks, monkeypatch):
        set_stocks([{"code": "000725", "name": "京东方A"}])
        monkeypatch.setattr(
            name_resolver, "_load_international_stock_map", lambda: [{"code": "09618.HK", "name": "京东集团-SW"}]
        )
        assert [s["code"] for s in name_resolver.resolve("京东")] == ["000725", "09618.HK"]

    def test_fuzzy_a_share_hit_does_not_hide_exact_us_name(self, set_stocks, monkeypatch):
        set_stocks([{"code": "600535", "name": "天士力"}])
        monkeypatch.setattr(
            name_resolver, "_load_international_stock_map", lambda: [{"code": "TSLA", "name": "特斯拉"}]
        )
        assert name_resolver.resolve("特斯拉") == [{"code": "TSLA", "name": "特斯拉"}]

    def test_exact_a_share_name_skips_international_maps(self, set_stocks, monkeypatch):
        set_stocks(DEFAULT_STOCKS)
        monkeypatch.setattr(name_resolver, "_load_international_stock_map", lambda: pytest.fail("not needed"))
        assert name_resolver.resolve("贵州茅台")[0]["code"] == "600519"

    def test_a_share_failure_does_not_hide_international_names(self, monkeypatch):
        def unavailable():
            raise RuntimeError("A provider down")

        monkeypatch.setattr(name_resolver, "_load_stock_map", unavailable)
        monkeypatch.setattr(
            name_resolver, "_load_international_stock_map", lambda: [{"code": "00700.HK", "name": "腾讯控股"}]
        )
        assert name_resolver.resolve("腾讯控股") == [{"code": "00700.HK", "name": "腾讯控股"}]
        with pytest.raises(RuntimeError, match="A provider down"):
            name_resolver.resolve("600519")  # a miss must surface the load error, not "no such stock"
        monkeypatch.setattr(
            name_resolver, "_load_international_stock_map", lambda: [{"code": "06199.HK", "name": "贵州银行"}]
        )
        with pytest.raises(RuntimeError, match="A provider down"):
            name_resolver.resolve("贵州茅台")  # a fuzzy HK hit is not an answer for an A-share name


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


def test_name_map_is_reused_across_cli_processes(tmp_path):
    import json
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    counter = tmp_path / "fetches.txt"
    script = textwrap.dedent("""\
        import json, sys
        from pathlib import Path
        from types import SimpleNamespace
        import pandas as pd

        def fetch():
            with Path(sys.argv[1]).open("a") as stream:
                stream.write("fetch\\n")
            return pd.DataFrame([{"代码": "600519", "名称": "贵州茅台"}])

        sys.modules["akshare"] = SimpleNamespace(stock_zh_a_spot_em=fetch)
        from tools.name_resolver import resolve
        print(json.dumps(resolve("贵州茅台")))
        """)
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script, str(counter)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "STOCK_CACHE_DIR": str(tmp_path / "cache")},
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == [{"code": "600519", "name": "贵州茅台"}]
    assert len(counter.read_text().splitlines()) == 1


@pytest.mark.parametrize("first", [[], [{"代码": "600519", "名称": None}], RuntimeError("provider down")])
def test_empty_invalid_and_failed_maps_are_not_cached(monkeypatch, tmp_path, first):
    import sys
    from types import SimpleNamespace

    import _cache
    import pandas as pd

    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    responses = iter([first, [{"代码": "600519", "名称": "贵州茅台"}]])

    def fetch():
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return pd.DataFrame(value)

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_spot_em=fetch))
    if first == []:
        assert name_resolver.resolve("600519") == []
    else:
        with pytest.raises(RuntimeError):
            name_resolver.resolve("600519")
    assert name_resolver.resolve("600519") == [{"code": "600519", "name": "贵州茅台"}]


def test_name_map_refreshes_after_one_day(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    import _cache
    import pandas as pd

    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    now = [1000.0]
    monkeypatch.setattr(_cache.time, "time", lambda: now[0])
    names = iter(["原名称", "新名称"])
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_spot_em=lambda: pd.DataFrame([{"代码": "600519", "名称": next(names)}])),
    )
    assert name_resolver.resolve("600519")[0]["name"] == "原名称"
    assert name_resolver.resolve("600519")[0]["name"] == "原名称"
    now[0] += 86401
    assert name_resolver.resolve("600519")[0]["name"] == "新名称"
