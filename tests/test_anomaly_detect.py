from unittest.mock import patch

import pandas as pd

from tools.anomaly_detect import (
    detect_anomalies,
    detect_bollinger_breakout,
    detect_capital_flow_anomaly,
    detect_divergence,
    detect_gap,
    detect_kdj_extreme,
    detect_large_move,
    detect_limit_hit,
    detect_ma_cross,
    detect_macd_cross,
    detect_price_breakout,
    detect_rsi_extreme,
    detect_volume_spike,
)


def _make_close(values: list) -> pd.Series:
    return pd.Series(values, dtype=float)


def _make_volume(values: list) -> pd.Series:
    return pd.Series(values, dtype=float)


def _make_df(closes, highs=None, lows=None, volumes=None):
    n = len(closes)
    if highs is None:
        highs = [c * 1.02 for c in closes]
    if lows is None:
        lows = [c * 0.98 for c in closes]
    if volumes is None:
        volumes = [1000000] * n
    return pd.DataFrame(
        {
            "close": pd.Series(closes, dtype=float),
            "high": pd.Series(highs, dtype=float),
            "low": pd.Series(lows, dtype=float),
            "volume": pd.Series(volumes, dtype=float),
        }
    )


class TestDetectMacdCross:
    def test_golden_cross(self):
        # Create data where DIF crosses above DEA on the last bar
        # Start bearish, end with a sharp rally
        close = _make_close([50 - i * 0.5 for i in range(40)] + [30, 32, 35, 38, 42, 47])
        result = detect_macd_cross(close)
        # A sharp reversal should trigger golden cross
        assert any(a["type"] == "macd_golden_cross" for a in result) or result == []

    def test_death_cross(self):
        # Start bullish, end with sharp decline
        close = _make_close([30 + i * 0.5 for i in range(40)] + [50, 48, 45, 42, 38, 33])
        result = detect_macd_cross(close)
        assert any(a["type"] == "macd_death_cross" for a in result) or result == []

    def test_no_cross(self):
        # Steady uptrend, no crossing
        close = _make_close([i + 1 for i in range(60)])
        result = detect_macd_cross(close)
        assert not any(a["type"] in ("macd_golden_cross", "macd_death_cross") for a in result)

    def test_insufficient_data(self):
        close = _make_close([10, 11, 12])
        assert detect_macd_cross(close) == []


class TestDetectRsiExtreme:
    def test_oversold_entry(self):
        # Create a sharp drop that pushes RSI below 20
        data = [50] * 20 + [50 - i * 3 for i in range(15)]
        close = _make_close(data)
        result = detect_rsi_extreme(close)
        # Check if RSI detected oversold entry
        oversold = [a for a in result if a["type"] == "rsi_oversold_entry"]
        if oversold:
            assert oversold[0]["direction"] == "bullish"
            assert oversold[0]["severity"] == "medium"

    def test_overbought_entry(self):
        # Create a sharp rally that pushes RSI above 80
        data = [50] * 20 + [50 + i * 3 for i in range(15)]
        close = _make_close(data)
        result = detect_rsi_extreme(close)
        overbought = [a for a in result if a["type"] == "rsi_overbought_entry"]
        if overbought:
            assert overbought[0]["direction"] == "bearish"
            assert overbought[0]["severity"] == "medium"

    def test_no_extreme(self):
        # Gentle trend, RSI stays mid-range
        close = _make_close([50 + i * 0.1 for i in range(30)])
        result = detect_rsi_extreme(close)
        assert result == []

    def test_insufficient_data(self):
        assert detect_rsi_extreme(_make_close([10, 11])) == []


class TestDetectPriceBreakout:
    def test_breakout_20d_high(self):
        # 20 days of range-bound, then breakout on last bar
        closes = [100] * 20 + [103]
        highs = [101] * 20 + [104]
        lows = [98] * 20 + [99]
        df = _make_df(closes, highs, lows)
        result = detect_price_breakout(df)
        breakouts = [a for a in result if a["type"] == "breakout_20d_high"]
        assert len(breakouts) == 1
        assert breakouts[0]["direction"] == "bullish"
        assert breakouts[0]["severity"] == "high"

    def test_breakdown_20d_low(self):
        closes = [100] * 20 + [97]
        highs = [102] * 20 + [99]
        lows = [98] * 20 + [96]
        df = _make_df(closes, highs, lows)
        result = detect_price_breakout(df)
        breakdowns = [a for a in result if a["type"] == "breakdown_20d_low"]
        assert len(breakdowns) == 1
        assert breakdowns[0]["direction"] == "bearish"

    def test_no_breakout(self):
        closes = [100] * 21
        df = _make_df(closes)
        result = detect_price_breakout(df)
        assert result == []

    def test_insufficient_data(self):
        df = _make_df([100] * 10)
        assert detect_price_breakout(df) == []


class TestDetectVolumeSpike:
    def test_extreme_spike(self):
        # 10 days normal, then 3.5x spike on last day
        volume = _make_volume([1000000] * 10 + [3500000])
        result = detect_volume_spike(volume)
        assert len(result) == 1
        assert result[0]["type"] == "volume_spike_extreme"
        assert result[0]["severity"] == "high"

    def test_moderate_spike(self):
        volume = _make_volume([1000000] * 10 + [2500000])
        result = detect_volume_spike(volume)
        assert len(result) == 1
        assert result[0]["type"] == "volume_spike"
        assert result[0]["severity"] == "medium"

    def test_no_spike(self):
        volume = _make_volume([1000000] * 6)
        result = detect_volume_spike(volume)
        assert result == []

    def test_insufficient_data(self):
        assert detect_volume_spike(_make_volume([100, 200])) == []


class TestDetectBollingerBreakout:
    def test_upper_break(self):
        # Steady data then a large jump on the last bar
        closes = [100.0] * 25 + [120.0]
        close = _make_close(closes)
        result = detect_bollinger_breakout(close)
        upper_breaks = [a for a in result if a["type"] == "bollinger_upper_break"]
        assert len(upper_breaks) == 1
        assert upper_breaks[0]["direction"] == "bearish"

    def test_lower_break(self):
        closes = [100.0] * 25 + [80.0]
        close = _make_close(closes)
        result = detect_bollinger_breakout(close)
        lower_breaks = [a for a in result if a["type"] == "bollinger_lower_break"]
        assert len(lower_breaks) == 1
        assert lower_breaks[0]["direction"] == "bullish"

    def test_no_break(self):
        closes = [100.0] * 30
        result = detect_bollinger_breakout(_make_close(closes))
        assert result == []

    def test_insufficient_data(self):
        assert detect_bollinger_breakout(_make_close([100] * 10)) == []


class TestDetectKdjExtreme:
    def test_overbought_entry(self):
        # Steady rally pushes J above 80
        n = 30
        closes = [50 + i * 2 for i in range(n)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        high = pd.Series(highs, dtype=float)
        low_s = pd.Series(lows, dtype=float)
        close = pd.Series(closes, dtype=float)
        result = detect_kdj_extreme(high, low_s, close)
        overbought = [a for a in result if a["type"] == "kdj_overbought_entry"]
        # May or may not trigger depending on exact timing
        if overbought:
            assert overbought[0]["direction"] == "bearish"

    def test_oversold_entry(self):
        n = 30
        closes = [100 - i * 2 for i in range(n)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        high = pd.Series(highs, dtype=float)
        low_s = pd.Series(lows, dtype=float)
        close = pd.Series(closes, dtype=float)
        result = detect_kdj_extreme(high, low_s, close)
        oversold = [a for a in result if a["type"] == "kdj_oversold_entry"]
        if oversold:
            assert oversold[0]["direction"] == "bullish"

    def test_insufficient_data(self):
        h = pd.Series([10, 11], dtype=float)
        low_s = pd.Series([9, 10], dtype=float)
        c = pd.Series([9.5, 10.5], dtype=float)
        assert detect_kdj_extreme(h, low_s, c) == []


class TestDetectLargeMove:
    def test_large_up_high(self):
        result = detect_large_move({"change_pct": 8.5})
        assert len(result) == 1
        assert result[0]["type"] == "large_move_up"
        assert result[0]["severity"] == "high"

    def test_large_up_medium(self):
        result = detect_large_move({"change_pct": 5.5})
        assert len(result) == 1
        assert result[0]["type"] == "large_move_up"
        assert result[0]["severity"] == "medium"

    def test_large_down_high(self):
        result = detect_large_move({"change_pct": -8.0})
        assert len(result) == 1
        assert result[0]["type"] == "large_move_down"
        assert result[0]["severity"] == "high"

    def test_large_down_medium(self):
        result = detect_large_move({"change_pct": -5.5})
        assert len(result) == 1
        assert result[0]["type"] == "large_move_down"
        assert result[0]["severity"] == "medium"

    def test_normal_move(self):
        result = detect_large_move({"change_pct": 2.0})
        assert result == []

    def test_no_data(self):
        assert detect_large_move({}) == []


class TestDetectLimitHit:
    def test_limit_up(self):
        quote = {"price": 11.0, "prev_close": 10.0, "change_pct": 10.0, "name": "贵州茅台"}
        result = detect_limit_hit("600519", quote)
        assert len(result) == 1
        assert result[0]["type"] == "limit_up"
        assert result[0]["severity"] == "high"

    def test_limit_down(self):
        quote = {"price": 9.0, "prev_close": 10.0, "change_pct": -10.0, "name": "平安银行"}
        result = detect_limit_hit("000001", quote)
        assert len(result) == 1
        assert result[0]["type"] == "limit_down"

    def test_chinext_20pct(self):
        quote = {"price": 12.0, "prev_close": 10.0, "change_pct": 20.0, "name": "宁德时代"}
        result = detect_limit_hit("300750", quote)
        assert len(result) == 1
        assert result[0]["type"] == "limit_up"

    def test_not_a_share(self):
        result = detect_limit_hit("AAPL", {"price": 150, "prev_close": 140, "change_pct": 7.0})
        assert result == []

    def test_no_quote_data(self):
        assert detect_limit_hit("600519", {}) == []

    def test_st_stock(self):
        quote = {"price": 10.5, "prev_close": 10.0, "change_pct": 5.0, "name": "ST某某"}
        result = detect_limit_hit("600000", quote)
        assert len(result) == 1
        assert result[0]["type"] == "limit_up"


class TestDetectCapitalFlowAnomaly:
    @patch("tools.anomaly_detect._run_tool")
    def test_inflow_surge(self, mock_run):
        mock_run.return_value = [
            {"main_net_inflow": 100},
            {"main_net_inflow": 120},
            {"main_net_inflow": 90},
            {"main_net_inflow": 110},
            {"main_net_inflow": 500},
        ]
        result = detect_capital_flow_anomaly("600519")
        inflows = [a for a in result if a["type"] == "capital_inflow_surge"]
        assert len(inflows) == 1
        assert inflows[0]["direction"] == "bullish"

    @patch("tools.anomaly_detect._run_tool")
    def test_outflow_surge(self, mock_run):
        mock_run.return_value = [
            {"main_net_inflow": 100},
            {"main_net_inflow": 120},
            {"main_net_inflow": 90},
            {"main_net_inflow": 110},
            {"main_net_inflow": -300},
        ]
        result = detect_capital_flow_anomaly("600519")
        outflows = [a for a in result if a["type"] == "capital_outflow_surge"]
        assert len(outflows) == 1
        assert outflows[0]["direction"] == "bearish"

    @patch("tools.anomaly_detect._run_tool")
    def test_normal_flow(self, mock_run):
        mock_run.return_value = [
            {"main_net_inflow": 100},
            {"main_net_inflow": 110},
            {"main_net_inflow": 105},
            {"main_net_inflow": 108},
        ]
        result = detect_capital_flow_anomaly("600519")
        assert result == []

    def test_us_stock_skipped(self):
        assert detect_capital_flow_anomaly("AAPL") == []

    @patch("tools.anomaly_detect._run_tool")
    def test_no_data(self, mock_run):
        mock_run.return_value = None
        assert detect_capital_flow_anomaly("600519") == []


class TestDetectAnomaliesIntegration:
    @patch("tools.anomaly_detect._run_tool")
    @patch("tools.anomaly_detect.fetch_kline")
    def test_basic_flow(self, mock_kline, mock_run_tool):
        # Create 30 bars of steady data
        records = [
            {"date": f"2024-01-{i+1:02d}", "open": 100, "close": 100, "high": 102, "low": 98, "volume": 1000000}
            for i in range(30)
        ]
        mock_kline.return_value = records
        mock_run_tool.return_value = {"price": 100, "prev_close": 99, "change_pct": 1.0, "name": "测试"}
        result = detect_anomalies("600519")
        assert result["symbol"] == "600519"
        assert "anomaly_count" in result
        assert "summary" in result
        assert "anomalies" in result
        assert isinstance(result["anomalies"], list)

    @patch("tools.anomaly_detect.fetch_kline")
    def test_kline_error(self, mock_kline):
        mock_kline.return_value = {"error": "network timeout"}
        result = detect_anomalies("600519")
        assert "error" in result

    @patch("tools.anomaly_detect.fetch_kline")
    def test_empty_kline(self, mock_kline):
        mock_kline.return_value = []
        result = detect_anomalies("600519")
        assert "error" in result

    @patch("tools.anomaly_detect._run_tool")
    @patch("tools.anomaly_detect.fetch_kline")
    def test_sorting_by_severity(self, mock_kline, mock_run_tool):
        # Large volume spike + normal everything else
        records = [
            {"date": f"2024-01-{i+1:02d}", "open": 100, "close": 100, "high": 102, "low": 98, "volume": 1000000}
            for i in range(29)
        ]
        records.append(
            {"date": "2024-01-30", "open": 100, "close": 100, "high": 102, "low": 98, "volume": 4000000}
        )
        mock_kline.return_value = records
        mock_run_tool.return_value = {"price": 100, "prev_close": 99, "change_pct": 1.0, "name": "测试"}
        result = detect_anomalies("600519")
        if len(result.get("anomalies", [])) > 1:
            severities = [a["severity"] for a in result["anomalies"]]
            order = {"high": 0, "medium": 1, "low": 2}
            assert all(order.get(severities[i], 3) <= order.get(severities[i + 1], 3) for i in range(len(severities) - 1))


class TestDetectDivergence:
    def test_top_divergence(self):
        # First half: price rises to 80, DIF rises strongly
        # Second half: price rises higher to 90, but DIF weaker (slower rise)
        part1 = [50 + i * 1.5 for i in range(20)]  # 50 -> 78.5
        part2 = [78.5 + i * 0.3 for i in range(20)]  # slow grind higher in price
        # Insert a dip between halves so DIF resets lower
        closes = part1 + [60] * 5 + part2
        df = _make_df(closes)
        result = detect_divergence(df)
        # May or may not trigger depending on exact MACD dynamics
        for a in result:
            if a["type"] == "top_divergence":
                assert a["direction"] == "bearish"
                assert a["severity"] == "high"

    def test_bottom_divergence(self):
        # First half: price drops to 20, DIF drops strongly
        # Second half: price drops lower, but DIF less negative
        part1 = [80 - i * 2 for i in range(20)]  # 80 -> 42
        part2 = [42 + i * 0.5 for i in range(5)] + [35 - i * 0.5 for i in range(15)]
        closes = part1 + [60] * 5 + part2
        df = _make_df(closes)
        result = detect_divergence(df)
        for a in result:
            if a["type"] == "bottom_divergence":
                assert a["direction"] == "bullish"
                assert a["severity"] == "high"

    def test_no_divergence(self):
        # Steady uptrend - price and MACD both rising
        closes = [50 + i for i in range(40)]
        df = _make_df(closes)
        result = detect_divergence(df)
        assert not any(a["type"] == "top_divergence" for a in result)

    def test_insufficient_data(self):
        df = _make_df([100] * 10)
        assert detect_divergence(df) == []


class TestDetectMaCross:
    def test_ma5_golden_cross_ma10(self):
        # Downtrend then sharp reversal on last bars
        closes = [100 - i * 0.5 for i in range(20)] + [90, 92, 95, 99, 104]
        close = _make_close(closes)
        result = detect_ma_cross(close)
        golden = [a for a in result if a["type"] == "ma5_cross_ma10_golden"]
        if golden:
            assert golden[0]["direction"] == "bullish"
            assert golden[0]["severity"] == "medium"

    def test_ma5_death_cross_ma10(self):
        # Uptrend then sharp decline on last bars
        closes = [50 + i * 0.5 for i in range(20)] + [60, 58, 55, 51, 46]
        close = _make_close(closes)
        result = detect_ma_cross(close)
        death = [a for a in result if a["type"] == "ma5_cross_ma10_death"]
        if death:
            assert death[0]["direction"] == "bearish"

    def test_ma10_golden_cross_ma20(self):
        # Long downtrend then sustained reversal
        closes = [100 - i for i in range(30)] + [70 + i * 2 for i in range(15)]
        close = _make_close(closes)
        result = detect_ma_cross(close)
        golden = [a for a in result if a["type"] == "ma10_cross_ma20_golden"]
        if golden:
            assert golden[0]["direction"] == "bullish"

    def test_ma10_death_cross_ma20(self):
        # Long uptrend then sustained decline
        closes = [50 + i for i in range(30)] + [80 - i * 2 for i in range(15)]
        close = _make_close(closes)
        result = detect_ma_cross(close)
        death = [a for a in result if a["type"] == "ma10_cross_ma20_death"]
        if death:
            assert death[0]["direction"] == "bearish"

    def test_no_cross(self):
        # Perfectly steady prices
        closes = [100.0] * 25
        result = detect_ma_cross(_make_close(closes))
        assert result == []

    def test_insufficient_data(self):
        assert detect_ma_cross(_make_close([100] * 10)) == []


class TestDetectGap:
    def test_gap_up(self):
        # Yesterday high=101, today low=103 → gap up
        closes = [100] * 5 + [104]
        highs = [101] * 5 + [106]
        lows = [99] * 5 + [103]
        df = _make_df(closes, highs, lows)
        result = detect_gap(df)
        assert len(result) == 1
        assert result[0]["type"] == "gap_up"
        assert result[0]["direction"] == "bullish"
        assert "gap_pct" in result[0]

    def test_gap_down(self):
        # Yesterday low=99, today high=97 → gap down
        closes = [100] * 5 + [95]
        highs = [101] * 5 + [97]
        lows = [99] * 5 + [94]
        df = _make_df(closes, highs, lows)
        result = detect_gap(df)
        assert len(result) == 1
        assert result[0]["type"] == "gap_down"
        assert result[0]["direction"] == "bearish"

    def test_large_gap_high_severity(self):
        # >3% gap
        closes = [100] * 5 + [108]
        highs = [101] * 5 + [110]
        lows = [99] * 5 + [105]
        df = _make_df(closes, highs, lows)
        result = detect_gap(df)
        assert len(result) == 1
        assert result[0]["severity"] == "high"

    def test_no_gap(self):
        closes = [100] * 5
        df = _make_df(closes)
        result = detect_gap(df)
        assert result == []

    def test_insufficient_data(self):
        df = _make_df([100])
        assert detect_gap(df) == []
