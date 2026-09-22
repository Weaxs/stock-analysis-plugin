from unittest.mock import patch

import pandas as pd

from tools.market_regime import _compute_indicators, _get_index_kline, classify_regime
from tools.stock_data import detect_market
from tools.technical import bollinger_series, macd_series, rsi_series


def test_a_share_regime_requests_explicit_shanghai_index():
    with patch("tools.market_regime._run_tool", return_value=[{"close": 1}]) as run_tool:
        assert _get_index_kline("A") == [{"close": 1}]
    run_tool.assert_called_once_with("stock_data.py", ["kline", "sh000001", "--period", "daily", "--count", "80"])


def test_hk_regime_requests_yahoo_hsi_symbol():
    # Yahoo quotes the Hang Seng Index as ^HSI; a bare "HSI" resolves to nothing.
    with patch("tools.market_regime._run_tool", return_value=[{"close": 1}]) as run_tool:
        assert _get_index_kline("HK") == [{"close": 1}]
    run_tool.assert_called_once_with("stock_data.py", ["kline", "^HSI", "--period", "daily", "--count", "80"])


def test_hsi_symbol_routes_to_yf_kline_chain():
    # detect_market has no caret-prefix branch: Yahoo index symbols fall through to
    # the default "US" bucket, which is exactly the yfinance/finnhub kline chain.
    assert detect_market("^HSI") == "US"
    assert detect_market("^GSPC") == "US"


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

    def test_macd_trend_matches_technical_macd_series(self, make_kline_data):
        # Consolidation pin: market_regime must use the same real EMA12-EMA26 as
        # technical.py (its old "EMA" was an np.mean simple average).
        klines = make_kline_data(80, "up")
        ind = _compute_indicators(klines)
        close = pd.Series([float(k["close"]) for k in klines])
        dif, _, _ = macd_series(close)
        assert ind["macd_trend"] == ("bullish" if float(dif.iloc[-1]) > 0 else "bearish")

    def test_rsi14_matches_technical_rsi_series(self, make_kline_data):
        klines = make_kline_data(80, "volatile")
        ind = _compute_indicators(klines)
        close = pd.Series([float(k["close"]) for k in klines])
        expected = rsi_series(close, 14).iloc[-1]
        if pd.isna(expected):
            assert ind["rsi14"] in (50.0, 100.0)
        else:
            assert ind["rsi14"] == round(float(expected), 1)

    def test_rsi14_all_up_bars_is_100(self):
        klines = [{"close": 10 + i, "high": 11 + i, "low": 9 + i} for i in range(80)]
        assert _compute_indicators(klines)["rsi14"] == 100.0

    def test_rsi14_flat_bars_is_neutral_not_overbought(self):
        # 14 flat bars also produce RSI NaN (zero gains AND zero losses) — mapping
        # that to 100 would misreport a dead-flat tape as an overbought extreme.
        klines = [{"close": 10, "high": 10, "low": 10}] * 80
        assert _compute_indicators(klines)["rsi14"] == 50.0

    def test_boll_width_uses_pandas_sample_std(self, make_kline_data):
        # Deliberate consolidation: BOLL comes from technical.bollinger_series
        # (pandas .std(), ddof=1) — not the old np.std (ddof=0), which made widths
        # systematically ~2.6% smaller. boll_width feeds the high_volatility
        # threshold, so pin the unified semantics.
        klines = make_kline_data(80, "volatile")
        ind = _compute_indicators(klines)
        close = pd.Series([float(k["close"]) for k in klines])
        upper, mid, lower = bollinger_series(close)
        assert ind["boll_width"] == round(float((upper.iloc[-1] - lower.iloc[-1]) / mid.iloc[-1]), 4)
