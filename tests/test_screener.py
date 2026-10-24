import json

import numpy as np
import pandas as pd
import pytest

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
    def test_cli_invalid_filter_exits_nonzero(self, monkeypatch, capsys):
        import sys

        monkeypatch.setattr(screener, "load_config", lambda *a: {"filters": {"pe_max": "bad"}})
        monkeypatch.setattr(sys, "argv", ["screener.py", "screen", "--config", "unused"])
        with pytest.raises(SystemExit) as exc:
            screener.main()
        assert exc.value.code == 1
        assert "error" in json.loads(capsys.readouterr().out)

    @pytest.mark.parametrize("filters", [{"price_max": 10}, {"pe_max": "bad"}, {"pe_max": float("nan")}])
    def test_invalid_filters_are_clean_errors(self, monkeypatch, filters):
        monkeypatch.setattr(screener, "fetch_snapshot", lambda *a: [{"symbol": "AAPL", "price": 100, "pe": 50}])
        monkeypatch.setattr(screener, "compute_sentiment", lambda *a: {"multiplier": 1})
        assert "error" in screen("US", top=20, config={"filters": filters})

    def test_explicit_unavailable_filter_is_not_claimed_as_applied(self, monkeypatch):
        monkeypatch.setattr(screener, "fetch_snapshot", lambda *a: [{"symbol": "AAPL", "price": 100, "change_pct": 1}])
        monkeypatch.setattr(screener, "compute_sentiment", lambda *a: {"multiplier": 1})
        result = screen("US", top=20, config={"filters": {"pe_max": 10}})
        assert "error" in result
        assert "pe" in result["error"]

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

    @pytest.mark.parametrize("factor", ["pe", "pb"])
    def test_non_positive_valuation_multiples_never_get_top_value_score(self, factor):
        result = compute_scores(pd.DataFrame({factor: [-50.0, 10.0, 20.0]}))
        assert result["value_score"].tolist() == [0, 100, 0]


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


class TestScreenErrorList:
    """snapshot_a/hk/us wrap a total failure as a one-row [{"error": ...}] list —
    screen() must surface it, not run the error row through the scoring pipeline
    as a ghost candidate."""

    def test_error_list_passthrough(self, monkeypatch):
        monkeypatch.setattr(screener, "fetch_snapshot", lambda market: [{"error": "A-share snapshot unavailable"}])
        result = screen("A", 20, None)
        # same error shape as the raw-None branch: error message + market key
        assert result == {"error": "A-share snapshot unavailable", "market": "A"}

    def test_error_list_no_ghost_candidate(self, monkeypatch):
        monkeypatch.setattr(screener, "fetch_snapshot", lambda market: [{"error": "all sources down"}])
        monkeypatch.setattr(screener, "compute_sentiment", lambda market, snapshot=None: _neutral_sentiment())
        result = screen("US", 20, None)
        assert "candidates" not in result
        assert result["error"] == "all sources down"


class TestNonFiniteSanitization:
    """An all-empty factor column min-maxes to NaN; NaN/±inf must become None —
    json.dumps would otherwise emit bare NaN/Infinity tokens that strict JSON
    parsers in the host adapters reject."""

    @pytest.mark.parametrize("missing_volume", [None, float("inf")])
    def test_missing_factor_leg_preserves_remaining_usable_scores(self, monkeypatch, missing_volume):
        snapshot = _snapshot()
        for row in snapshot:
            row["volume"] = missing_volume
        monkeypatch.setattr(screener, "fetch_snapshot", lambda market: snapshot)
        monkeypatch.setattr(screener, "compute_sentiment", lambda market, snapshot=None: _neutral_sentiment())
        result = screen("A", 20, None)
        assert result["returned_count"] == 3
        for c in result["candidates"]:
            for value in c["scores"].values():
                assert value is None or (isinstance(value, (int, float)) and np.isfinite(value))
            assert c["scores"]["liquidity"] == {"AAA": 100, "BBB": 50, "CCC": 0}[c["symbol"]]
            assert c["scores"]["composite"] is not None
            assert c["scores"]["final"] is not None
        # the whole payload must be strict-JSON serializable (allow_nan=False)
        json.dumps(result, allow_nan=False)

    def test_finite_scores_unchanged(self, monkeypatch):
        _patch_screen(monkeypatch)
        result = screen("A", 20, None)
        assert result["candidates"][0]["scores"]["composite"] == 100.0
        assert all(v is not None for c in result["candidates"] for v in c["scores"].values())

    def test_l2_path_with_sanitized_scores(self, monkeypatch):
        snapshot = _snapshot()
        for row in snapshot:
            row["volume"] = None
        monkeypatch.setattr(screener, "fetch_snapshot", lambda market: snapshot)
        monkeypatch.setattr(screener, "compute_sentiment", lambda market, snapshot=None: _neutral_sentiment())
        monkeypatch.setattr(screener, "_enrich_one", lambda symbol, market: {})
        result = screen("A", 20, None, l2=True)
        # L2 rewrites final from the (coerced) numeric components — no NaN, no crash
        for c in result["candidates"]:
            assert isinstance(c["scores"]["final"], (int, float))
        json.dumps(result, allow_nan=False)


def test_dividend_filter_and_factor_are_opt_in(monkeypatch):
    raw = [
        {
            "symbol": "600519",
            "pe": 10,
            "pb": 2,
            "market_cap": 1e10,
            "turnover_rate": 1,
            "change_pct": 0,
            "volume_ratio": 1,
        },
        {
            "symbol": "000001",
            "pe": 10,
            "pb": 2,
            "market_cap": 1e10,
            "turnover_rate": 1,
            "change_pct": 0,
            "volume_ratio": 1,
        },
    ]
    monkeypatch.setattr(screener, "fetch_snapshot", lambda m: raw)
    monkeypatch.setattr(screener, "compute_sentiment", lambda *a, **k: {"multiplier": 1})
    monkeypatch.setattr(
        "value_data.dividend_ranking",
        lambda **k: {
            "report_date": "2025-12-31",
            "items": [{"symbol": "600519", "dividend_yield": 2}, {"symbol": "000001", "dividend_yield": 5}],
        },
    )
    result = screen("A", 20, {"filters": {"dividend_yield_min": 3}})
    assert [r["symbol"] for r in result["candidates"]] == ["000001"]
    assert result["candidates"][0]["dividend_yield"] == 5
    assert "dividend" in result["candidates"][0]["scores"]


def test_requested_dividend_filter_does_not_silently_skip_missing_source(monkeypatch):
    monkeypatch.setattr(screener, "fetch_snapshot", lambda m: [{"symbol": "600519", "pe": 10}])
    monkeypatch.setattr(screener, "compute_sentiment", lambda *a, **k: {"multiplier": 1})

    def fail(**_):
        raise ValueError("down")

    monkeypatch.setattr("value_data.dividend_ranking", fail)
    assert "error" in screen("A", 20, {"filters": {"dividend_yield_min": 3}})


@pytest.mark.parametrize("threshold", ["3%", None, float("nan"), True])
def test_invalid_dividend_threshold_fails_before_network(monkeypatch, threshold):
    from unittest.mock import MagicMock

    snapshot = MagicMock(return_value=[{"symbol": "600519", "pe": 10, "pb": 2, "market_cap": 1e10}])
    monkeypatch.setattr(screener, "fetch_snapshot", snapshot)
    monkeypatch.setattr(screener, "compute_sentiment", lambda *a, **k: {"multiplier": 1})
    monkeypatch.setattr(
        "value_data.dividend_ranking",
        lambda **k: {"report_date": "2025-12-31", "items": [{"symbol": "600519", "dividend_yield": 5}]},
    )
    result = screen("A", 20, {"filters": {"dividend_yield_min": threshold}})
    assert "error" in result
    snapshot.assert_not_called()


def test_hk_dividend_factor_normalizes_hk_symbols_and_preserves_basis(monkeypatch):
    raw = [
        {
            "symbol": "0005.HK",
            "pe": 10,
            "pb": 2,
            "market_cap": 1e10,
            "turnover_rate": 1,
            "change_pct": 0,
            "volume_ratio": 1,
        }
    ]
    monkeypatch.setattr(screener, "fetch_snapshot", lambda m: raw)
    monkeypatch.setattr(screener, "compute_sentiment", lambda *a, **k: {"multiplier": 1})
    calls = []

    def ranking(**kwargs):
        calls.append(kwargs)
        return {
            "report_date": "2025-12-31",
            "yield_basis": "HKEX fiscal-year ratio",
            "items": [{"symbol": "00005.HK", "dividend_yield": 5}],
        }

    monkeypatch.setattr("value_data.dividend_ranking", ranking)
    result = screen("HK", 20, {"filters": {"dividend_yield_min": 3}})
    assert result["candidates"][0]["dividend_yield"] == 5
    assert result["dividend_factor"]["yield_basis"] == "HKEX fiscal-year ratio"
    assert calls[0]["market"] == "HK"


def test_l2_coverage_exposes_missing_groups_and_actual_weights(monkeypatch):
    _patch_screen(monkeypatch)
    monkeypatch.setattr(screener, "_enrich_one", lambda symbol, market: {})
    result = screen("A", 20, None, l2=True)
    candidate = result["candidates"][0]
    coverage = candidate["factor_coverage"]
    assert coverage["available"] == ["value", "liquidity"]
    assert set(coverage["missing"]) == {"quality", "growth", "momentum", "moneyflow", "low_volatility"}
    assert coverage["ratio"] == 0.25
    assert coverage["effective_weights"] == {"value": 0.6, "liquidity": 0.4}
    assert candidate["scores"]["final"] == 100


def test_l2_missing_financials_do_not_receive_constant_quality_score(monkeypatch, make_kline_data):
    _patch_screen(monkeypatch)
    kline = make_kline_data(n=2)

    def fetch(args, **kwargs):
        if args[0] == "kline":
            return kline
        if args[0] == "fundamental_context" and args[1] == "AAA":
            return {"profitability": {"roe": 0.15}}
        return {"error": "unavailable"}

    monkeypatch.setattr(screener, "_fetch_json", fetch)
    candidates = {c["symbol"]: c for c in screen("A", 20, None, l2=True)["candidates"]}
    assert candidates["AAA"]["scores"]["quality"] == 50.0
    bbb = candidates["BBB"]
    assert "quality" not in bbb["scores"]
    assert "quality" in bbb["factor_coverage"]["missing"]
    assert bbb["factor_coverage"]["available"] == ["value", "liquidity", "momentum"]
    assert bbb["factor_coverage"]["ratio"] == 0.45


def test_no_factor_data_is_unknown_instead_of_zero_score(monkeypatch):
    _patch_screen(monkeypatch)
    monkeypatch.setattr(screener, "fetch_snapshot", lambda market: [{"symbol": "TEST", "price": 10}])
    for l2 in (False, True):
        monkeypatch.setattr(screener, "_enrich_one", lambda symbol, market: {})
        candidate = screen("A", 20, None, l2=l2)["candidates"][0]
        assert candidate["scores"]["value"] is None
        assert candidate["scores"]["final"] is None
        assert candidate["factor_coverage"]["available"] == []
        assert candidate["factor_coverage"]["effective_weights"] == {}


def test_l2_fetch_can_finish_after_old_thirty_second_limit(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    def run(command, **kwargs):
        if kwargs["timeout"] < 90:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout='{"roe":0.2}')

    monkeypatch.setattr("tools._subproc.subprocess.run", run)
    assert screener._fetch_json(["financials", "AAPL"]) == {"roe": 0.2}


def test_l2_final_is_explainable_from_returned_effective_scores(monkeypatch):
    _patch_screen(monkeypatch)
    monkeypatch.setattr(screener, "_enrich_one", lambda symbol, market: {"ret_20d": 0 if symbol == "AAA" else 20})
    result = screen("A", 20, None, l2=True)
    for candidate in result["candidates"]:
        coverage = candidate["factor_coverage"]
        scores = coverage["effective_scores"]
        weights = coverage["effective_weights"]
        assert scores.keys() == weights.keys()
        reconstructed = (
            sum(scores[group] * weights[group] for group in scores) * result["market_sentiment"]["multiplier"]
        )
        assert round(reconstructed, 1) == candidate["scores"]["final"]
    aaa = next(c for c in result["candidates"] if c["symbol"] == "AAA")
    assert aaa["factor_coverage"]["effective_scores"]["momentum"] == 0
    assert aaa["scores"]["momentum"] == 100  # L1 reference is kept for compatibility


@pytest.mark.parametrize("top", [-1, 0, True, 1.5])
def test_invalid_top_fails_before_collecting(monkeypatch, top):
    from tools import screener

    monkeypatch.setattr(screener, "fetch_snapshot", lambda *a, **kw: pytest.fail("invalid top reached data source"))
    assert "error" in screener.screen("A", top, None, l2=True)


def test_l1_missing_factors_do_not_count_as_zero_before_ranking(monkeypatch):
    from tools import screener

    monkeypatch.setattr(
        screener,
        "fetch_snapshot",
        lambda market: [
            {"symbol": "AAA", "price": 10, "change_pct": 1, "volume": 100},
            {"symbol": "BBB", "price": 10, "change_pct": 5, "volume": 500},
        ],
    )
    monkeypatch.setattr(screener, "compute_sentiment", lambda *a: {"multiplier": 1})
    result = screener.screen("US", 1, None)
    best = result["candidates"][0]
    assert best["symbol"] == "BBB"
    assert best["scores"]["composite"] == 100
    assert best["scores"]["final"] == 100
    assert best["scores"]["value"] is None
    assert best["factor_coverage"]["effective_weights"] == {"momentum": 0.5, "liquidity": 0.5}
