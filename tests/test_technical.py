import numpy as np
import pandas as pd

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
