from tools.market_regime import _compute_indicators, classify_regime


class TestClassifyRegime:
    def test_trending_up(self, bullish_indicators):
        regime, conf, ma_arr = classify_regime(bullish_indicators)
        assert regime == "trending_up"
        assert conf >= 0.85
        assert ma_arr == "bullish"

    def test_trending_down(self, bearish_indicators):
        regime, conf, ma_arr = classify_regime(bearish_indicators)
        assert regime == "trending_down"
        assert conf >= 0.85
        assert ma_arr == "bearish"

    def test_sideways(self, sideways_indicators):
        regime, conf, ma_arr = classify_regime(sideways_indicators)
        assert regime == "sideways"
        assert conf >= 0.7

    def test_volatile(self):
        ind = {
            "ma5": 101,
            "ma10": 100.5,
            "ma20": 100,
            "ma60": 99,
            "close": 101,
            "change_pct": 2.0,
            "atr_ratio": 0.03,
            "boll_width": 0.18,
            "rsi14": 55,
            "macd_trend": "bullish",
            "ma_spread_pct": 1.0,
        }
        regime, conf, _ = classify_regime(ind)
        assert regime == "volatile"
        assert conf >= 0.5

    def test_mixed(self):
        ind = {
            "ma5": 101,
            "ma10": 99,
            "ma20": 100,
            "ma60": 98,
            "close": 101,
            "change_pct": 0.5,
            "atr_ratio": 0.015,
            "boll_width": 0.08,
            "rsi14": 50,
            "macd_trend": "bullish",
            "ma_spread_pct": 5.0,
        }
        regime, conf, _ = classify_regime(ind)
        assert regime == "mixed"

    def test_confidence_capped(self, bullish_indicators):
        bullish_indicators["ma_spread_pct"] = 10.0
        _, conf, _ = classify_regime(bullish_indicators)
        assert conf <= 0.95


class TestComputeIndicators:
    def test_sufficient_data(self, make_kline_data):
        klines = make_kline_data(80, "up")
        ind = _compute_indicators(klines)
        assert "ma5" in ind
        assert "ma20" in ind
        assert "atr_ratio" in ind
        assert "rsi14" in ind
        assert "boll_width" in ind
        assert ind["ma5"] > 0

    def test_insufficient_data(self):
        klines = [{"close": 10, "high": 11, "low": 9}] * 10
        ind = _compute_indicators(klines)
        assert ind == {}

    def test_uptrend_ma_order(self, make_kline_data):
        klines = make_kline_data(80, "up")
        ind = _compute_indicators(klines)
        assert ind["ma5"] > ind["ma20"]
