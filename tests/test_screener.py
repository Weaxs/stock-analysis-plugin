import pandas as pd

import tools.screener as screener
from tools.screener import (
    DEFAULT_FILTERS,
    _minmax,
    apply_filters,
    compute_scores,
    compute_sentiment,
    merge_filters,
    screen,
    sentiment_multiplier,
)


class TestMergeFilters:
    def test_defaults(self):
        result = merge_filters(None)
        assert result == DEFAULT_FILTERS

    def test_override(self):
        result = merge_filters({"filters": {"pe_max": 50}})
        assert result["pe_max"] == 50
        assert result["pe_min"] == 0

    def test_empty_config(self):
        result = merge_filters({})
        assert result == DEFAULT_FILTERS


class TestApplyFilters:
    def test_pe_filter(self):
        df = pd.DataFrame(
            {
                "pe": [10, 50, 150, -5],
                "pb": [2, 5, 8, 1],
                "market_cap": [1e10] * 4,
            }
        )
        filters = merge_filters(None)
        result = apply_filters(df, filters)
        assert len(result) == 2
        assert 150 not in result["pe"].values
        assert -5 not in result["pe"].values

    def test_no_matching(self):
        df = pd.DataFrame({"pe": [200, 300]})
        filters = merge_filters(None)
        result = apply_filters(df, filters)
        assert len(result) == 0

    def test_empty_df(self):
        df = pd.DataFrame(columns=["pe", "pb"])
        filters = merge_filters(None)
        result = apply_filters(df, filters)
        assert len(result) == 0


class TestMinmax:
    def test_uniform(self):
        s = pd.Series([5.0, 5.0, 5.0])
        result = _minmax(s)
        assert all(result == 50.0)

    def test_range(self):
        s = pd.Series([0.0, 50.0, 100.0])
        result = _minmax(s)
        assert abs(result.iloc[0] - 0) < 0.01
        assert abs(result.iloc[1] - 50) < 0.01
        assert abs(result.iloc[2] - 100) < 0.01


class TestComputeScores:
    def test_scores_computed(self):
        df = pd.DataFrame(
            {
                "pe": [10.0, 20.0, 30.0],
                "pb": [1.0, 2.0, 3.0],
                "change_pct": [5.0, 3.0, 1.0],
                "volume_ratio": [2.0, 1.5, 1.0],
                "turnover_rate": [3.0, 2.0, 1.0],
                "volume": [1e6, 8e5, 5e5],
            }
        )
        result = compute_scores(df)
        assert "value_score" in result.columns
        assert "momentum_score" in result.columns
        assert "liquidity_score" in result.columns
        assert "composite_score" in result.columns
        assert result["value_score"].iloc[0] > result["value_score"].iloc[2]

    def test_missing_columns(self):
        df = pd.DataFrame({"pe": [10.0, 20.0], "change_pct": [5.0, 3.0]})
        result = compute_scores(df)
        assert "composite_score" in result.columns


def _snapshot():
    return [
        {
            "symbol": "AAA",
            "name": "Alpha",
            "price": 10.0,
            "change_pct": 3.0,
            "volume": 1e6,
            "turnover_rate": 2.0,
            "pe": 10.0,
            "pb": 1.0,
            "market_cap": 1e10,
            "volume_ratio": 1.5,
        },
        {
            "symbol": "BBB",
            "name": "Beta",
            "price": 20.0,
            "change_pct": 1.0,
            "volume": 8e5,
            "turnover_rate": 1.5,
            "pe": 20.0,
            "pb": 2.0,
            "market_cap": 2e10,
            "volume_ratio": 1.2,
        },
        {
            "symbol": "CCC",
            "name": "Gamma",
            "price": 30.0,
            "change_pct": 0.5,
            "volume": 5e5,
            "turnover_rate": 1.0,
            "pe": 30.0,
            "pb": 3.0,
            "market_cap": 3e10,
            "volume_ratio": 0.8,
        },
    ]


def _neutral_sentiment(multiplier=1.0):
    return {"score": 50.0, "level": "neutral", "signal": "yellow", "multiplier": multiplier}


def _patch_screen(monkeypatch, multiplier=1.0):
    monkeypatch.setattr(screener, "fetch_snapshot", lambda market: _snapshot())
    monkeypatch.setattr(screener, "compute_sentiment", lambda market, snapshot=None: _neutral_sentiment(multiplier))


class TestSentimentMultiplier:
    def test_endpoints(self):
        assert sentiment_multiplier(0) == 0.8
        assert sentiment_multiplier(100) == 1.2

    def test_midpoint(self):
        assert sentiment_multiplier(50) == 1.0


class TestComputeSentiment:
    def test_degrades_on_fetch_failure(self, monkeypatch):
        monkeypatch.setattr(screener, "_fetch_json", lambda args, timeout=30: None)
        result = compute_sentiment("A", snapshot=_snapshot())
        assert result == {"score": None, "level": "unknown", "signal": None, "multiplier": 1.0}

    def test_degrades_on_error_dict(self, monkeypatch):
        monkeypatch.setattr(screener, "_fetch_json", lambda args, timeout=30: {"error": "boom"})
        result = compute_sentiment("US")
        assert result["multiplier"] == 1.0
        assert result["level"] == "unknown"

    def test_unknown_market(self):
        result = compute_sentiment("JP")
        assert result["multiplier"] == 1.0

    def test_a_share_uses_snapshot_and_indices(self, monkeypatch):
        snapshot = _snapshot()
        stats = {"up_count": 80, "down_count": 20, "limit_up_count": 30, "limit_down_count": 10}
        calls = {}

        def _stats(data):
            calls["data"] = data
            return stats

        monkeypatch.setattr(screener, "compute_market_stats", _stats)
        monkeypatch.setattr(
            screener,
            "_fetch_json",
            lambda args, timeout=30: [{"code": "sh000001", "change_pct": 2.0}],
        )
        result = compute_sentiment("A", snapshot=snapshot)
        assert calls["data"] is snapshot
        # breadth 80 (w .45) + index 74 (w .35) + limit 75 (w .20) = 76.9
        assert result["score"] == 76.9
        assert result["level"] == "constructive"
        assert result["signal"] == "green"
        assert result["multiplier"] == round(0.8 + 76.9 / 100 * 0.4, 3)

    def test_us_indices_only(self, monkeypatch):
        def _no_stats(data):
            raise AssertionError("compute_market_stats must not be called without a snapshot")

        monkeypatch.setattr(screener, "compute_market_stats", _no_stats)
        monkeypatch.setattr(screener, "_fetch_json", lambda args, timeout=30: [{"code": "^GSPC", "change_pct": 1.0}])
        result = compute_sentiment("US")
        # index-only: 50 + 1.0 * 12 = 62 → ≥ 60 is constructive
        assert result["score"] == 62.0
        assert result["level"] == "constructive"
        assert result["signal"] == "green"
        assert result["multiplier"] == round(0.8 + 62.0 / 100 * 0.4, 3)

    def test_us_snapshot_adds_breadth(self, monkeypatch):
        snapshot = _snapshot()
        calls = {}

        def _stats(data):
            calls["data"] = data
            # non-A snapshot: normalize_stock_code gives limit_pct=None → no limit counts
            return {"up_count": 3, "down_count": 0, "limit_up_count": 0, "limit_down_count": 0}

        monkeypatch.setattr(screener, "compute_market_stats", _stats)
        monkeypatch.setattr(screener, "_fetch_json", lambda args, timeout=30: [{"code": "^GSPC", "change_pct": 1.0}])
        result = compute_sentiment("US", snapshot=snapshot)
        assert calls["data"] is snapshot
        # breadth 100 (w .45) + index 62 (w .35); limit_total=0 drops the limit component,
        # weights renormalize: (45 + 21.7) / 0.8 = 83.375 → 83.4
        assert result["score"] == 83.4
        assert result["level"] == "constructive"
        assert result["signal"] == "green"
        assert result["multiplier"] == round(0.8 + 83.4 / 100 * 0.4, 3)


class TestScreenNoL2Regression:
    def test_output_shape_and_sorting_unchanged(self, monkeypatch):
        _patch_screen(monkeypatch)
        result = screen("A", 20, None)
        assert result["l2_enabled"] is False
        assert result["market_sentiment"]["multiplier"] == 1.0
        candidates = result["candidates"]
        assert [c["symbol"] for c in candidates] == ["AAA", "BBB", "CCC"]
        for c in candidates:
            assert set(c["scores"]) == {"value", "momentum", "liquidity", "composite", "final"}
            assert "enriched" not in c
        assert candidates[0]["scores"]["composite"] == 100.0
        assert candidates[1]["scores"]["composite"] == 48.1
        assert candidates[2]["scores"]["composite"] == 0.0
        # multiplier 1.0 → final equals composite
        for c in candidates:
            assert c["scores"]["final"] == c["scores"]["composite"]

    def test_sentiment_multiplier_applied_to_final(self, monkeypatch):
        _patch_screen(monkeypatch, multiplier=1.2)
        result = screen("A", 20, None)
        candidates = result["candidates"]
        assert candidates[0]["scores"]["final"] == 120.0
        assert candidates[1]["scores"]["final"] == 57.7
        # composite untouched by the multiplier
        assert candidates[0]["scores"]["composite"] == 100.0

    def test_config_sort_by_still_applies_without_l2(self, monkeypatch):
        _patch_screen(monkeypatch)
        result = screen("A", 20, {"sort_by": "value_score", "sort_order": "asc"})
        assert [c["symbol"] for c in result["candidates"]] == ["CCC", "BBB", "AAA"]


class TestScreenSnapshotErrors:
    def test_fetch_failure_returns_error(self, monkeypatch):
        monkeypatch.setattr(screener, "fetch_snapshot", lambda market: None)
        result = screen("A", 20, None)
        assert result == {"error": "failed to fetch market snapshot", "market": "A"}

    def test_error_dict_passthrough(self, monkeypatch):
        monkeypatch.setattr(screener, "fetch_snapshot", lambda market: {"error": "provider down"})
        assert screen("US", 20, None) == {"error": "provider down"}


_L2_ENRICH = {
    "AAA": {
        "ret_20d": 10.0,
        "ret_60d": 20.0,
        "high_250_proximity": 0.95,
        "vol20": 0.2,
        "roe": 20.0,
        "gross_margin": 40.0,
        "net_margin": 15.0,
        "revenue_yoy": 30.0,
        "net_income_yoy": 25.0,
        "main_net_5d": 1e8,
    },
    "BBB": {
        "ret_20d": 5.0,
        "ret_60d": 10.0,
        "high_250_proximity": 0.85,
        "vol20": 0.3,
        "roe": 10.0,
        "gross_margin": 30.0,
        "net_margin": 10.0,
        "revenue_yoy": 10.0,
        "net_income_yoy": 5.0,
        "main_net_5d": 0.0,
    },
    "CCC": {},
}


class TestScreenL2:
    def _run(self, monkeypatch, enrich=None, config=None, l2=False, multiplier=1.0):
        _patch_screen(monkeypatch, multiplier=multiplier)
        data = _L2_ENRICH if enrich is None else enrich
        monkeypatch.setattr(screener, "_enrich_one", lambda symbol, market: dict(data.get(symbol, {})))
        return screen("A", 20, config, l2=l2)

    def test_factor_scores_and_final(self, monkeypatch):
        result = self._run(monkeypatch, config={"l2": True})
        assert result["l2_enabled"] is True
        candidates = result["candidates"]
        assert [c["symbol"] for c in candidates] == ["AAA", "BBB", "CCC"]
        assert [c["rank"] for c in candidates] == [1, 2, 3]

        aaa, bbb, ccc = candidates
        assert aaa["enriched"] is True
        assert ccc["enriched"] is False

        # AAA wins every cross-sectional factor
        assert aaa["scores"]["quality"] == 100.0
        assert aaa["scores"]["growth"] == 100.0
        assert aaa["scores"]["moneyflow"] == 100.0
        assert aaa["scores"]["low_volatility"] == 100.0
        assert bbb["scores"]["quality"] == 0.0
        assert bbb["scores"]["growth"] == 0.0
        assert bbb["scores"]["moneyflow"] == 0.0
        assert bbb["scores"]["low_volatility"] == 0.0

        # momentum key keeps the L1 value
        assert aaa["scores"]["momentum"] == 100.0
        assert bbb["scores"]["momentum"] == 38.6

        # composite_l2: AAA = 100 (all groups max); BBB = 0.15*50 + 0.10*55 = 13.0
        assert aaa["scores"]["final"] == 100.0
        assert bbb["scores"]["final"] == 13.0
        # CCC: only value/liquidity groups, all zero
        assert ccc["scores"]["final"] == 0.0

        # CCC has no L2 data → no factor keys at all
        for key in ("quality", "growth", "moneyflow", "low_volatility"):
            assert key not in ccc["scores"]

    def test_cli_flag_enables_l2(self, monkeypatch):
        result = self._run(monkeypatch, l2=True)
        assert result["l2_enabled"] is True
        assert result["candidates"][0]["scores"]["quality"] == 100.0

    def test_final_uses_composite_l2_times_multiplier(self, monkeypatch):
        result = self._run(monkeypatch, config={"l2": True}, multiplier=1.2)
        candidates = result["candidates"]
        assert candidates[0]["scores"]["final"] == 120.0
        assert candidates[1]["scores"]["final"] == 15.6

    def test_weight_renormalization_when_factors_missing(self, monkeypatch):
        # No enrichment data at all → only value/liquidity groups (momentum no longer falls back to L1)
        result = self._run(monkeypatch, config={"l2": True}, enrich={})
        candidates = result["candidates"]
        assert all(c["enriched"] is False for c in candidates)
        assert all("growth" not in c["scores"] for c in candidates)
        # renormalized over weights 0.15 + 0.10 = 0.25
        # AAA: (15 + 10) / 0.25 = 100; BBB: (7.5 + 5.5) / 0.25 = 52.0
        assert candidates[0]["scores"]["final"] == 100.0
        assert candidates[1]["scores"]["final"] == 52.0

    def test_candidate_missing_momentum_drops_group(self, monkeypatch):
        # CCC has quality data but no kline factors → momentum group absent for CCC only;
        # its composite renormalizes over value (0) + liquidity (0) + quality (50).
        enrich = dict(_L2_ENRICH, CCC={"roe": 15.0})
        result = self._run(monkeypatch, config={"l2": True}, enrich=enrich)
        candidates = {c["symbol"]: c for c in result["candidates"]}
        ccc = candidates["CCC"]
        assert ccc["scores"]["quality"] == 50.0
        assert ccc["scores"]["momentum"] == 0.0  # L1 value kept in scores, but not in composite
        # (0.20 * 50) / (0.15 + 0.10 + 0.20) = 22.2
        assert ccc["scores"]["final"] == 22.2
        for key in ("growth", "moneyflow", "low_volatility"):
            assert key not in ccc["scores"]
        # CCC (22.2) outranks BBB (13.0)
        assert [c["symbol"] for c in result["candidates"]] == ["AAA", "CCC", "BBB"]

    def test_sort_by_config_ignored_with_l2(self, monkeypatch):
        result = self._run(monkeypatch, config={"l2": True, "sort_by": "value_score", "sort_order": "asc"})
        assert [c["symbol"] for c in result["candidates"]] == ["AAA", "BBB", "CCC"]
