import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def make_kline_data():
    """Generate synthetic kline data for testing."""

    def _make(n: int = 60, trend: str = "up", base: float = 10.0):
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        rng = np.random.default_rng(42)
        close = np.empty(n)
        close[0] = base
        for i in range(1, n):
            if trend == "up":
                close[i] = close[i - 1] * (1 + rng.uniform(0, 0.03))
            elif trend == "down":
                close[i] = close[i - 1] * (1 - rng.uniform(0, 0.03))
            elif trend == "flat":
                close[i] = close[i - 1] * (1 + rng.uniform(-0.005, 0.005))
            elif trend == "volatile":
                close[i] = close[i - 1] * (1 + rng.uniform(-0.05, 0.05))
            else:
                close[i] = close[i - 1]

        high = close * (1 + rng.uniform(0.005, 0.02, n))
        low = close * (1 - rng.uniform(0.005, 0.02, n))
        open_ = close * (1 + rng.uniform(-0.01, 0.01, n))
        volume = rng.integers(1_000_000, 10_000_000, n).astype(float)

        records = []
        for i in range(n):
            records.append(
                {
                    "date": dates[i],
                    "open": round(float(open_[i]), 2),
                    "high": round(float(high[i]), 2),
                    "low": round(float(low[i]), 2),
                    "close": round(float(close[i]), 2),
                    "volume": float(volume[i]),
                }
            )
        return records

    return _make


@pytest.fixture
def bullish_indicators():
    return {
        "ma5": 105,
        "ma10": 103,
        "ma20": 100,
        "ma60": 95,
        "close": 106,
        "change_pct": 1.5,
        "atr_ratio": 0.015,
        "boll_width": 0.08,
        "rsi14": 60,
        "macd_trend": "bullish",
        "ma_spread_pct": 5.0,
    }


@pytest.fixture
def bearish_indicators():
    return {
        "ma5": 95,
        "ma10": 97,
        "ma20": 100,
        "ma60": 105,
        "close": 94,
        "change_pct": -1.5,
        "atr_ratio": 0.015,
        "boll_width": 0.08,
        "rsi14": 40,
        "macd_trend": "bearish",
        "ma_spread_pct": 5.0,
    }


@pytest.fixture
def sideways_indicators():
    return {
        "ma5": 100.5,
        "ma10": 100.2,
        "ma20": 100,
        "ma60": 99.5,
        "close": 100.3,
        "change_pct": 0.1,
        "atr_ratio": 0.01,
        "boll_width": 0.05,
        "rsi14": 50,
        "macd_trend": "bullish",
        "ma_spread_pct": 0.5,
    }
