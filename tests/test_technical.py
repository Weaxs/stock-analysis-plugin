import json
import sys

import numpy as np
import pandas as pd

import tools.technical as technical_mod
from tools.technical import (
    calc_bias,
    calc_bollinger,
    calc_kdj,
    calc_ma,
    calc_ma_support,
    calc_macd,
    calc_rsi,
    calc_trend,
    calc_volume,
    calc_volume_direction,
    generate_signal_score,
    to_dataframe,
)


def _make_close(values: list) -> pd.Series:
    return pd.Series(values, dtype=float)


def _make_volume(values: list) -> pd.Series:
    return pd.Series(values, dtype=float)


class TestToDataframe:
    def test_basic(self):
        records = [
            {"date": "2024-01-01", "open": "10", "close": "11", "high": "12", "low": "9", "volume": "1000"},
            {"date": "2024-01-02", "open": "11", "close": "12", "high": "13", "low": "10", "volume": "2000"},
        ]
        df = to_dataframe(records)
        assert len(df) == 2
        assert np.issubdtype(df["close"].dtype, np.number)
        assert np.issubdtype(df["volume"].dtype, np.number)

    def test_sorts_by_date(self):
        records = [
            {"date": "2024-01-03", "open": 10, "close": 11, "high": 12, "low": 9, "volume": 1000},
            {"date": "2024-01-01", "open": 10, "close": 10, "high": 12, "low": 9, "volume": 1000},
        ]
        df = to_dataframe(records)
        assert df.iloc[0]["close"] == 10


class TestFetchKline:
    def test_routes_through_run_tool(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            technical_mod, "run_tool", lambda script, args, **kw: calls.append((script, args)) or [{"close": 1}]
        )
        assert technical_mod.fetch_kline("600519", "weekly", 30) == [{"close": 1}]
        assert calls == [("stock_data.py", ["kline", "600519", "--period", "weekly", "--count", "30"])]

    def test_failure_returns_none_not_exception(self, monkeypatch):
        # run_tool already swallows timeout/non-zero-exit/invalid-JSON into None;
        # fetch_kline must surface that instead of crashing on empty stdout.
        monkeypatch.setattr(technical_mod, "run_tool", lambda *a, **kw: None)
        assert technical_mod.fetch_kline("600519") is None

    def test_analyze_failure_is_clean_error(self, monkeypatch):
        monkeypatch.setattr(technical_mod, "run_tool", lambda *a, **kw: None)
        assert technical_mod.analyze("600519") == {"error": "No kline data returned"}

    def test_error_dict_from_child_propagates(self, monkeypatch):
        monkeypatch.setattr(technical_mod, "run_tool", lambda *a, **kw: {"error": "no data for symbol"})
        assert technical_mod.analyze("600519") == {"error": "no data for symbol"}


class TestCalcMa:
    def test_bullish_arrangement(self):
        close = _make_close([i + 1 for i in range(60)])
        result = calc_ma(close)
        assert result["ma5"] > result["ma10"] > result["ma20"]
        assert result["ma_arrangement"] == "bullish"

    def test_bearish_arrangement(self):
        close = _make_close([60 - i for i in range(60)])
        result = calc_ma(close)
        assert result["ma5"] < result["ma10"] < result["ma20"]
        assert result["ma_arrangement"] == "bearish"

    def test_insufficient_data(self):
        close = _make_close([10, 11])
        result = calc_ma(close)
        assert result["ma_arrangement"] == "insufficient_data"


class TestCalcMacd:
    def test_uptrend_bullish(self):
        close = _make_close([10 + i * 0.5 for i in range(60)])
        result = calc_macd(close)
        assert result["signal"] == "bullish"
        assert result["dif"] > result["dea"]
        assert "dif" in result
        assert "dea" in result
        assert "macd" in result

    def test_downtrend_bearish(self):
        close = _make_close([40 - i * 0.5 for i in range(60)])
        result = calc_macd(close)
        assert result["signal"] == "bearish"


class TestCalcRsi:
    def test_uptrend_high_rsi(self):
        values = [10 + i * 0.5 for i in range(50)]
        for i in range(0, 50, 5):
            values[i] -= 1.0
        close = _make_close(values)
        result = calc_rsi(close)
        assert result["rsi6"] is not None
        assert result["rsi6"] > 50

    def test_overbought_signal(self):
        values = [10 + i * 2 for i in range(50)]
        for i in range(0, 50, 5):
            values[i] -= 3.0
        close = _make_close(values)
        result = calc_rsi(close)
        assert result["signal"] in ("overbought", "approaching_overbought")

    def test_keys_present(self):
        close = _make_close([10 + i * 0.1 for i in range(30)])
        result = calc_rsi(close)
        assert "rsi6" in result
        assert "rsi12" in result
        assert "rsi24" in result
        assert "signal" in result


class TestCalcBollinger:
    def test_keys_present(self):
        close = _make_close([10 + i * 0.1 for i in range(30)])
        result = calc_bollinger(close)
        assert "upper" in result
        assert "mid" in result
        assert "lower" in result
        assert "position" in result
        assert "bandwidth" in result
        assert result["upper"] > result["mid"] > result["lower"]


class TestCalcKdj:
    def test_keys_present(self):
        n = 30
        high = pd.Series([12 + i * 0.1 for i in range(n)])
        low = pd.Series([8 + i * 0.1 for i in range(n)])
        close = pd.Series([10 + i * 0.1 for i in range(n)])
        result = calc_kdj(high, low, close)
        assert "k" in result
        assert "d" in result
        assert "j" in result
        assert "signal" in result


class TestCalcVolume:
    def test_normal_volume(self):
        volume = _make_volume([1_000_000] * 20)
        result = calc_volume(volume)
        assert result["signal"] == "normal"
        assert abs(result["volume_ratio"] - 1.0) < 0.01

    def test_heavy_volume(self):
        volume = _make_volume([1_000_000] * 19 + [3_000_000])
        result = calc_volume(volume)
        assert result["signal"] in ("heavy", "extremely_heavy")


class TestCalcTrend:
    def test_bullish(self):
        ma = {"ma5": 105, "ma10": 103, "ma20": 100, "ma60": 95}
        result = calc_trend(ma)
        assert result["overall"] == "bullish"

    def test_bearish(self):
        ma = {"ma5": 95, "ma10": 97, "ma20": 100, "ma60": 105}
        result = calc_trend(ma)
        assert result["overall"] == "bearish"

    def test_neutral(self):
        ma = {"ma5": 101, "ma10": 99, "ma20": 100, "ma60": 102}
        result = calc_trend(ma)
        assert result["overall"] == "neutral"

    def test_with_none(self):
        ma = {"ma5": 100, "ma10": None, "ma20": 99, "ma60": 98}
        result = calc_trend(ma)
        assert "short_term" in result
        assert result["short_term"] == "unknown"


class TestCalcBias:
    def test_positive_bias(self):
        close = _make_close([100])
        ma_data = {"ma5": 95, "ma10": 90, "ma20": 85}
        result = calc_bias(close, ma_data)
        assert result["bias_ma5"] > 0
        assert result["bias_ma10"] > 0

    def test_negative_bias(self):
        close = _make_close([90])
        ma_data = {"ma5": 95, "ma10": 100, "ma20": 105}
        result = calc_bias(close, ma_data)
        assert result["bias_ma5"] < 0

    def test_none_ma(self):
        close = _make_close([100])
        ma_data = {"ma5": None, "ma10": 100, "ma20": 100}
        result = calc_bias(close, ma_data)
        assert result["bias_ma5"] is None


class TestCalcVolumeDirection:
    def test_heavy_volume_up(self):
        close = _make_close([10] * 9 + [11])
        volume = _make_volume([100_000] * 9 + [200_000])
        result = calc_volume_direction(close, volume)
        assert result == "heavy_volume_up"

    def test_heavy_volume_down(self):
        close = _make_close([10] * 9 + [9])
        volume = _make_volume([100_000] * 9 + [200_000])
        result = calc_volume_direction(close, volume)
        assert result == "heavy_volume_down"

    def test_shrink_volume_down(self):
        close = _make_close([10] * 9 + [9.5])
        volume = _make_volume([100_000] * 9 + [50_000])
        result = calc_volume_direction(close, volume)
        assert result == "shrink_volume_down"

    def test_single_point(self):
        close = _make_close([10])
        volume = _make_volume([100_000])
        result = calc_volume_direction(close, volume)
        assert result == "normal"


class TestCalcMaSupport:
    def test_at_ma5(self):
        close = _make_close([100])
        ma_data = {"ma5": 100, "ma10": 95}
        result = calc_ma_support(close, ma_data)
        assert result["support_ma5"] is True

    def test_far_from_ma(self):
        close = _make_close([100])
        ma_data = {"ma5": 80, "ma10": 75}
        result = calc_ma_support(close, ma_data)
        assert result["support_ma5"] is False
        assert result["support_ma10"] is False


class TestGenerateSignalScore:
    def test_bullish_high_score(self):
        ma = {"ma5": 105, "ma10": 103, "ma20": 100, "ma60": 95, "ma_arrangement": "bullish"}
        macd = {"signal": "bullish", "dif": 1.5, "dea": 1.0, "macd": 1.0, "cross": "golden_cross"}
        rsi = {"rsi6": 25, "rsi12": 30, "signal": "approaching_oversold"}
        vol = {"signal": "normal", "volume_ratio": 1.0}
        trend = {"short_term": "bullish", "medium_term": "bullish", "long_term": "bullish", "overall": "bullish"}
        bias = {"bias_ma5": 1.0, "bias_ma10": 2.0, "bias_ma20": 5.0}
        close = _make_close([95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105])
        volume = _make_volume([1_000_000] * 11)

        result = generate_signal_score(ma, macd, rsi, vol, trend, bias, close, volume)
        assert result["signal_score"] >= 60
        assert result["buy_signal"] in ("STRONG_BUY", "BUY")
        assert isinstance(result["signal_reasons"], list)

    def test_bearish_low_score(self):
        ma = {"ma5": 95, "ma10": 97, "ma20": 100, "ma60": 105, "ma_arrangement": "bearish"}
        macd = {"signal": "bearish", "dif": -1.5, "dea": -1.0, "macd": -1.0, "cross": "death_cross"}
        rsi = {"rsi6": 75, "rsi12": 72, "signal": "approaching_overbought"}
        vol = {"signal": "heavy", "volume_ratio": 2.5}
        trend = {"short_term": "bearish", "medium_term": "bearish", "long_term": "bearish", "overall": "bearish"}
        bias = {"bias_ma5": 8.0, "bias_ma10": 10.0, "bias_ma20": 12.0}
        close = _make_close([105, 104, 103, 102, 101, 100, 99, 98, 97, 96, 95])
        volume = _make_volume([1_000_000] * 10 + [3_000_000])

        result = generate_signal_score(ma, macd, rsi, vol, trend, bias, close, volume)
        assert result["signal_score"] < 30
        assert result["buy_signal"] in ("SELL", "STRONG_SELL")


class TestParsePeriodsArg:
    def test_valid_csv(self):
        assert technical_mod.parse_periods_arg("daily,weekly") == ["daily", "weekly"]

    def test_strips_whitespace(self):
        assert technical_mod.parse_periods_arg(" daily , monthly ") == ["daily", "monthly"]

    def test_invalid_value(self):
        result = technical_mod.parse_periods_arg("daily,hourly")
        assert "error" in result
        assert "hourly" in result["error"]

    def test_duplicate(self):
        assert "error" in technical_mod.parse_periods_arg("daily,daily")

    def test_empty(self):
        assert "error" in technical_mod.parse_periods_arg("")
        assert "error" in technical_mod.parse_periods_arg(" , ")


class TestMultiPeriodAnalysis:
    def test_analyze_default_has_no_multi_period(self, monkeypatch, make_kline_data):
        monkeypatch.setattr(technical_mod, "run_tool", lambda *a, **kw: make_kline_data(120))
        result = technical_mod.analyze("600519")
        assert "multi_period" not in result
        assert result["period"] == "daily"

    def test_summaries_and_resonance(self, monkeypatch, make_kline_data):
        monkeypatch.setattr(technical_mod, "run_tool", lambda *a, **kw: make_kline_data(120, trend="up"))
        result = technical_mod.analyze_multi("600519", "daily", 120, ["daily", "weekly"])
        assert result["period"] == "daily"
        mp = result["multi_period"]
        assert [s["period"] for s in mp["periods"]] == ["daily", "weekly"]
        daily = mp["periods"][0]
        # compact summary picks scalars from the full result, no big nested objects
        assert daily["trend_overall"] == result["trend"]["overall"]
        assert daily["ma_arrangement"] == result["moving_averages"]["ma_arrangement"]
        assert daily["macd_signal"] == result["macd"]["signal"]
        assert daily["rsi_signal"] == result["rsi"]["signal"]
        assert "moving_averages" not in daily
        assert "signals" not in daily
        res = mp["resonance"]
        assert res["direction"] == "aligned_bullish"
        assert res == {"direction": "aligned_bullish", "bullish": 2, "bearish": 0, "neutral": 0, "total": 2}

    def test_divergent_periods(self, monkeypatch, make_kline_data):
        def fake_run(script, args, **kw):
            period = args[args.index("--period") + 1]
            return make_kline_data(120, trend="down" if period == "weekly" else "up")

        monkeypatch.setattr(technical_mod, "run_tool", fake_run)
        result = technical_mod.analyze_multi("600519", "daily", 120, ["daily", "weekly"])
        res = result["multi_period"]["resonance"]
        assert res["direction"] == "divergent"
        assert res["bullish"] == 1
        assert res["bearish"] == 1

    def test_reuses_primary_period_fetch(self, monkeypatch, make_kline_data):
        calls = []

        def fake_run(script, args, **kw):
            calls.append(args)
            return make_kline_data(120)

        monkeypatch.setattr(technical_mod, "run_tool", fake_run)
        technical_mod.analyze_multi("600519", "daily", 120, ["daily", "weekly"])
        assert len(calls) == 2

    def test_period_error_isolated(self, monkeypatch, make_kline_data):
        def fake_run(script, args, **kw):
            if args[args.index("--period") + 1] == "weekly":
                return None
            return make_kline_data(120, trend="up")

        monkeypatch.setattr(technical_mod, "run_tool", fake_run)
        result = technical_mod.analyze_multi("600519", "daily", 120, ["daily", "weekly"])
        weekly = result["multi_period"]["periods"][1]
        assert weekly["period"] == "weekly"
        assert "error" in weekly
        assert result["multi_period"]["resonance"]["total"] == 1

    def test_primary_error_returns_plain_error(self, monkeypatch):
        monkeypatch.setattr(technical_mod, "run_tool", lambda *a, **kw: None)
        result = technical_mod.analyze_multi("600519", "daily", 120, ["daily", "weekly"])
        assert result == {"error": "No kline data returned"}

    def test_cli_default_output_unchanged(self, monkeypatch, capsys, make_kline_data):
        monkeypatch.setattr(technical_mod, "run_tool", lambda *a, **kw: make_kline_data(120, trend="up"))
        monkeypatch.setattr(sys, "argv", ["technical.py", "analyze", "600519"])
        technical_mod.main()
        out = json.loads(capsys.readouterr().out)
        assert "multi_period" not in out

    def test_cli_with_periods_adds_multi_period(self, monkeypatch, capsys, make_kline_data):
        monkeypatch.setattr(technical_mod, "run_tool", lambda *a, **kw: make_kline_data(120, trend="up"))
        monkeypatch.setattr(sys, "argv", ["technical.py", "analyze", "600519", "--periods", "daily,weekly"])
        technical_mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["period"] == "daily"
        assert out["multi_period"]["resonance"]["direction"] == "aligned_bullish"

    def test_cli_invalid_periods_prints_error(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["technical.py", "analyze", "600519", "--periods", "hourly"])
        technical_mod.main()
        out = json.loads(capsys.readouterr().out)
        assert "error" in out
        assert "hourly" in out["error"]


class TestCalcResonance:
    """calc_resonance over hand-built summaries — aligned only on unanimous
    bullish/bearish; neutral is counted but never aligns."""

    def test_aligned_bearish(self):
        summaries = [
            {"period": "daily", "trend_overall": "bearish"},
            {"period": "weekly", "trend_overall": "bearish"},
        ]
        res = technical_mod.calc_resonance(summaries)
        assert res == {"direction": "aligned_bearish", "bullish": 0, "bearish": 2, "neutral": 0, "total": 2}

    def test_neutral_breaks_alignment_but_is_counted(self):
        summaries = [
            {"period": "daily", "trend_overall": "bearish"},
            {"period": "weekly", "trend_overall": "neutral"},
        ]
        res = technical_mod.calc_resonance(summaries)
        assert res == {"direction": "divergent", "bullish": 0, "bearish": 1, "neutral": 1, "total": 2}

    def test_all_neutral_is_divergent_not_aligned(self):
        summaries = [
            {"period": "daily", "trend_overall": "neutral"},
            {"period": "weekly", "trend_overall": "neutral"},
        ]
        res = technical_mod.calc_resonance(summaries)
        assert res == {"direction": "divergent", "bullish": 0, "bearish": 0, "neutral": 2, "total": 2}
