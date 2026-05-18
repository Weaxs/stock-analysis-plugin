from tools.pattern import (
    _body,
    _change_pct,
    _is_bearish,
    _is_bullish,
    _lower_shadow,
    _range,
    _upper_shadow,
    detect_bearish_engulfing,
    detect_big_candle,
    detect_box_oscillation,
    detect_breakdown,
    detect_breakout,
    detect_bullish_engulfing,
    detect_doji,
    detect_evening_star,
    detect_hammer,
    detect_morning_star,
    detect_shooting_star,
    to_dataframe,
)


def _make_row(open_, high, low, close):
    return {"open": open_, "high": high, "low": low, "close": close}


class TestHelpers:
    def test_body(self):
        assert _body(_make_row(10, 12, 9, 11)) == 1

    def test_range(self):
        assert _range(_make_row(10, 12, 9, 11)) == 3

    def test_upper_shadow(self):
        assert _upper_shadow(_make_row(10, 12, 9, 11)) == 1

    def test_lower_shadow(self):
        assert _lower_shadow(_make_row(10, 12, 9, 11)) == 1

    def test_is_bullish(self):
        assert _is_bullish(_make_row(10, 12, 9, 11)) is True
        assert _is_bullish(_make_row(11, 12, 9, 10)) is False

    def test_is_bearish(self):
        assert _is_bearish(_make_row(11, 12, 9, 10)) is True
        assert _is_bearish(_make_row(10, 12, 9, 11)) is False

    def test_change_pct(self):
        assert abs(_change_pct(_make_row(10, 12, 9, 11)) - 10.0) < 0.01

    def test_change_pct_zero_open(self):
        assert _change_pct(_make_row(0, 12, 0, 11)) == 0


class TestDetectDoji:
    def test_doji_found(self):
        records = [
            {"date": "2024-01-01", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_doji(df)
        assert len(patterns) == 1
        assert patterns[0]["pattern"] == "doji"

    def test_no_doji(self):
        records = [
            {"date": "2024-01-01", "open": 10.0, "high": 11.0, "low": 9.5, "close": 11.0, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_doji(df)
        assert len(patterns) == 0


class TestDetectHammer:
    def test_hammer_after_bearish(self):
        records = [
            {"date": "2024-01-01", "open": 11.0, "high": 11.0, "low": 10.0, "close": 10.0, "volume": 1000},
            {"date": "2024-01-02", "open": 10.0, "high": 10.0, "low": 8.8, "close": 9.9, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_hammer(df)
        assert len(patterns) == 1
        assert patterns[0]["direction"] == "bullish"


class TestDetectShootingStar:
    def test_shooting_star_after_bullish(self):
        records = [
            {"date": "2024-01-01", "open": 10.0, "high": 11.0, "low": 10.0, "close": 11.0, "volume": 1000},
            {"date": "2024-01-02", "open": 11.0, "high": 12.5, "low": 11.0, "close": 11.1, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_shooting_star(df)
        assert len(patterns) == 1
        assert patterns[0]["direction"] == "bearish"


class TestDetectBigCandle:
    def test_big_bullish(self):
        records = [
            {"date": "2024-01-01", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.35, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_big_candle(df)
        assert len(patterns) == 1
        assert patterns[0]["direction"] == "bullish"
        assert "大阳线" in patterns[0]["name_cn"]

    def test_big_bearish(self):
        records = [
            {"date": "2024-01-01", "open": 10.0, "high": 10.2, "low": 9.5, "close": 9.65, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_big_candle(df)
        assert len(patterns) == 1
        assert patterns[0]["direction"] == "bearish"


class TestDetectMorningStar:
    def test_morning_star(self):
        records = [
            {"date": "2024-01-01", "open": 11.0, "high": 11.0, "low": 9.5, "close": 9.5, "volume": 1000},
            {"date": "2024-01-02", "open": 9.5, "high": 9.6, "low": 9.4, "close": 9.45, "volume": 1000},
            {"date": "2024-01-03", "open": 9.6, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_morning_star(df)
        assert len(patterns) == 1
        assert patterns[0]["direction"] == "bullish"


class TestDetectEveningStar:
    def test_evening_star(self):
        records = [
            {"date": "2024-01-01", "open": 9.5, "high": 11.0, "low": 9.5, "close": 11.0, "volume": 1000},
            {"date": "2024-01-02", "open": 11.0, "high": 11.2, "low": 10.9, "close": 11.1, "volume": 1000},
            {"date": "2024-01-03", "open": 11.0, "high": 11.0, "low": 9.5, "close": 10.0, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_evening_star(df)
        assert len(patterns) == 1
        assert patterns[0]["direction"] == "bearish"


class TestDetectEngulfing:
    def test_bullish_engulfing(self):
        records = [
            {"date": "2024-01-01", "open": 10.5, "high": 10.5, "low": 10.0, "close": 10.0, "volume": 1000},
            {"date": "2024-01-02", "open": 10.0, "high": 10.8, "low": 9.9, "close": 10.6, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_bullish_engulfing(df)
        assert len(patterns) == 1

    def test_bearish_engulfing(self):
        records = [
            {"date": "2024-01-01", "open": 10.0, "high": 10.5, "low": 10.0, "close": 10.5, "volume": 1000},
            {"date": "2024-01-02", "open": 10.5, "high": 10.6, "low": 9.8, "close": 9.9, "volume": 1000},
        ]
        df = to_dataframe(records)
        patterns = detect_bearish_engulfing(df)
        assert len(patterns) == 1


class TestDetectBreakout:
    def test_breakout(self, make_kline_data):
        records = make_kline_data(30, "flat", base=10.0)
        records[-1]["close"] = 15.0
        records[-1]["high"] = 15.5
        records[-2]["close"] = 10.0
        df = to_dataframe(records)
        patterns = detect_breakout(df)
        assert len(patterns) >= 1
        assert patterns[0]["direction"] == "bullish"


class TestDetectBreakdown:
    def test_breakdown(self, make_kline_data):
        records = make_kline_data(30, "flat", base=10.0)
        records[-1]["close"] = 5.0
        records[-1]["low"] = 4.5
        records[-2]["close"] = 10.0
        df = to_dataframe(records)
        patterns = detect_breakdown(df)
        assert len(patterns) >= 1
        assert patterns[0]["direction"] == "bearish"


class TestDetectBoxOscillation:
    def test_box(self):
        records = []
        for i in range(15):
            records.append(
                {
                    "date": f"2024-01-{i + 1:02d}",
                    "open": 10.0,
                    "high": 10.3,
                    "low": 9.8,
                    "close": 10.1,
                    "volume": 1000,
                }
            )
        df = to_dataframe(records)
        patterns = detect_box_oscillation(df)
        assert len(patterns) >= 1
        assert patterns[0]["pattern"] == "box_oscillation"
