"""Unit tests for tools/volume_analysis.py analyze_volume() — offline, fetch_kline stubbed."""

import pandas as pd
import pytest

import tools.volume_analysis as volume_analysis

SYMBOL = "600519"
RISING_20 = [10 + i * 0.01 for i in range(20)]


def make_records(closes, volumes):
    assert len(closes) == len(volumes)
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    return [
        {
            "date": dates[i].strftime("%Y-%m-%d"),
            "open": closes[i],
            "high": closes[i] * 1.01,
            "low": closes[i] * 0.99,
            "close": closes[i],
            "volume": float(volumes[i]),
        }
        for i in range(len(closes))
    ]


@pytest.fixture
def set_kline(monkeypatch):
    def _set(records):
        monkeypatch.setattr(volume_analysis, "fetch_kline", lambda *args, **kwargs: records)

    return _set


def pattern_types(result):
    return [p["type"] for p in result["patterns"]]


class TestDataBoundaries:
    def test_fetch_error_passthrough(self, set_kline):
        set_kline({"error": "all sources down"})
        assert volume_analysis.analyze_volume(SYMBOL) == {"error": "all sources down"}

    def test_empty_records(self, set_kline):
        set_kline([])
        assert volume_analysis.analyze_volume(SYMBOL) == {"error": "No kline data returned"}

    def test_insufficient_data_below_10_rows(self, set_kline):
        set_kline(make_records([10.0] * 9, [100.0] * 9))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result == {"error": "Insufficient data: 9 rows (need at least 10)"}

    def test_minimum_10_rows_analyzes(self, set_kline):
        set_kline(make_records([10 + i * 0.01 for i in range(10)], [100.0 + i for i in range(10)]))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert "error" not in result
        assert result["data_points"] == 10
        assert result["vol_ratio_20d"] is None  # 20日均量需要至少20行

    def test_conftest_synthetic_kline(self, set_kline, make_kline_data):
        set_kline(make_kline_data(60, trend="up"))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert "error" not in result
        assert result["data_points"] == 60
        assert result["vol_ratio_5d"] is not None


class TestLatestDayPatterns:
    def test_heavy_volume_rally(self, set_kline):
        set_kline(make_records(RISING_20, [100.0] * 19 + [300.0]))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["symbol"] == SYMBOL
        assert result["vol_ratio_5d"] == 2.14
        assert result["vol_ratio_20d"] == 2.73
        pattern = next(p for p in result["patterns"] if p["type"] == "heavy_volume_rally")
        assert pattern["signal"] == "bullish"

    def test_heavy_volume_decline(self, set_kline):
        set_kline(make_records(RISING_20[:-1] + [9.0], [100.0] * 19 + [300.0]))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["vol_ratio_5d"] == 2.14
        pattern = next(p for p in result["patterns"] if p["type"] == "heavy_volume_decline")
        assert pattern["signal"] == "bearish"

    def test_shrink_volume_decline(self, set_kline):
        set_kline(make_records(RISING_20[:-1] + [9.0], [100.0] * 19 + [20.0]))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["vol_ratio_5d"] == 0.24
        pattern = next(p for p in result["patterns"] if p["type"] == "shrink_volume_decline")
        assert pattern["signal"] == "neutral_to_bullish"

    def test_shrink_volume_rally(self, set_kline):
        set_kline(make_records(RISING_20, [100.0] * 19 + [20.0]))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["vol_ratio_5d"] == 0.24
        pattern = next(p for p in result["patterns"] if p["type"] == "shrink_volume_rally")
        assert pattern["signal"] == "weak_bullish"


class TestVolumeBiasAndCorrelation:
    def test_bullish_volume_bias(self, set_kline):
        set_kline(make_records([10.0, 11.0] * 10, [100.0, 200.0] * 10))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["avg_volume_up_days"] == 200
        assert result["avg_volume_down_days"] == 100
        assert result["up_down_volume_ratio"] == 2.0
        assert "bullish_volume_bias" in pattern_types(result)

    def test_bearish_volume_bias(self, set_kline):
        set_kline(make_records([10.0, 11.0] * 10, [200.0, 100.0] * 10))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["avg_volume_up_days"] == 100
        assert result["avg_volume_down_days"] == 200
        assert result["up_down_volume_ratio"] == 0.5
        assert "bearish_volume_bias" in pattern_types(result)

    def test_positive_vol_price_correlation(self, set_kline):
        set_kline(make_records([10.0, 11.0] * 10, [100.0, 200.0] * 10))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["vol_price_correlation"] == pytest.approx(1.0)
        assert "positive_vol_price_corr" in pattern_types(result)

    def test_vol_price_divergence(self, set_kline):
        set_kline(make_records([10.0, 11.0] * 10, [200.0, 100.0] * 10))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["vol_price_correlation"] == pytest.approx(-1.0)
        assert "vol_price_divergence" in pattern_types(result)


class TestVolumeTrend:
    def test_increasing(self, set_kline):
        set_kline(make_records(RISING_20, [100.0] * 15 + [200.0] * 5))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["volume_trend"] == "increasing"
        assert result["volume_trend_ratio"] == 2.0

    def test_decreasing(self, set_kline):
        set_kline(make_records(RISING_20, [200.0] * 15 + [100.0] * 5))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["volume_trend"] == "decreasing"
        assert result["volume_trend_ratio"] == 0.5

    def test_stable(self, set_kline):
        set_kline(make_records(RISING_20, [100.0 + (i % 3) * 10 for i in range(20)]))
        result = volume_analysis.analyze_volume(SYMBOL)
        assert result["volume_trend"] == "stable"
