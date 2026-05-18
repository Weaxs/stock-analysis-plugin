import pandas as pd

from tools.backtest import (
    _substitute,
    check_condition,
    compute_ema,
    compute_ma,
    compute_metrics,
    compute_rsi,
    diagnose,
    sample_curve,
)


class TestComputeRsi:
    def test_uptrend_high_rsi(self):
        close = pd.Series([10 + i * 0.5 for i in range(50)])
        rsi = compute_rsi(close, 14)
        assert float(rsi.iloc[-1]) >= 50

    def test_downtrend_low_rsi(self):
        close = pd.Series([30 - i * 0.5 for i in range(30)])
        rsi = compute_rsi(close, 14)
        assert float(rsi.iloc[-1]) < 50


class TestComputeMa:
    def test_ma_value(self):
        close = pd.Series([10.0] * 20)
        ma = compute_ma(close, 5)
        assert abs(float(ma.iloc[-1]) - 10.0) < 0.01

    def test_ma_ascending(self):
        close = pd.Series([float(i) for i in range(1, 21)])
        ma = compute_ma(close, 5)
        assert float(ma.iloc[-1]) == 18.0


class TestComputeEma:
    def test_ema_value(self):
        close = pd.Series([10.0] * 20)
        ema = compute_ema(close, 12)
        assert abs(float(ema.iloc[-1]) - 10.0) < 0.01


class TestCheckCondition:
    def test_greater_than(self):
        s = pd.Series([10, 20, 30, 40, 50])
        assert check_condition(s, ">", 25, 2) is True
        assert check_condition(s, ">", 35, 2) is False

    def test_less_than(self):
        s = pd.Series([50, 40, 30, 20, 10])
        assert check_condition(s, "<", 35, 2) is True

    def test_cross_above(self):
        s = pd.Series([10, 20, 30, 40, 50])
        assert check_condition(s, "cross_above", 25, 2) is True
        assert check_condition(s, "cross_above", 15, 2) is False

    def test_cross_below(self):
        s = pd.Series([50, 40, 30, 20, 10])
        assert check_condition(s, "cross_below", 35, 2) is True

    def test_out_of_bounds(self):
        s = pd.Series([10, 20])
        assert check_condition(s, ">", 5, 0) is False
        assert check_condition(s, ">", 5, 10) is False

    def test_equals(self):
        s = pd.Series([10, 20, 30])
        assert check_condition(s, "==", 20, 1) is True


class TestSubstitute:
    def test_simple(self):
        result = _substitute("{fast_period}", {"fast_period": 5})
        assert result == 5

    def test_no_match(self):
        result = _substitute("hello", {"fast_period": 5})
        assert result == "hello"

    def test_float(self):
        result = _substitute("{threshold}", {"threshold": 0.5})
        assert result == 0.5


class TestSampleCurve:
    def test_short_curve(self):
        curve = [{"date": f"d{i}", "equity": i} for i in range(10)]
        result = sample_curve(curve, 200)
        assert len(result) == 10

    def test_long_curve(self):
        curve = [{"date": f"d{i}", "equity": i} for i in range(500)]
        result = sample_curve(curve, 200)
        assert len(result) <= 201
        assert result[-1] == curve[-1]


class TestComputeMetrics:
    def test_basic_metrics(self):
        trades = [
            {"type": "buy", "date": "2024-01-01", "price": 10, "shares": 100, "amount": 1000},
            {
                "type": "sell",
                "date": "2024-02-01",
                "price": 12,
                "shares": 100,
                "amount": 1200,
                "pnl": 200,
                "pnl_pct": 0.2,
                "holding_days": 31,
            },
            {"type": "buy", "date": "2024-03-01", "price": 11, "shares": 100, "amount": 1100},
            {
                "type": "sell",
                "date": "2024-04-01",
                "price": 10,
                "shares": 100,
                "amount": 1000,
                "pnl": -100,
                "pnl_pct": -0.09,
                "holding_days": 31,
            },
        ]
        curve = [{"date": f"2024-01-{i + 1:02d}", "equity": 100000 + i * 10} for i in range(90)]
        metrics = compute_metrics(trades, curve, 100000, 110000, "2024-01-01", "2024-04-01")

        assert metrics["total_trades"] == 2
        assert metrics["winning_trades"] == 1
        assert metrics["losing_trades"] == 1
        assert abs(metrics["win_rate"] - 0.5) < 0.01
        assert metrics["total_return"] > 0

    def test_empty_trades(self):
        curve = [{"date": "2024-01-01", "equity": 100000}]
        metrics = compute_metrics([], curve, 100000, 100000, "2024-01-01", "2024-12-31")
        assert metrics["total_trades"] == 0
        assert metrics["win_rate"] == 0


class TestDiagnose:
    def test_good_strategy(self):
        metrics = {
            "win_rate": 0.65,
            "profit_loss_ratio": 2.5,
            "max_drawdown": -0.08,
            "sharpe_ratio": 1.8,
            "total_trades": 20,
            "winning_trades": 13,
            "losing_trades": 7,
            "avg_holding_days": 5,
            "max_consecutive_wins": 5,
            "max_consecutive_losses": 2,
            "total_return": 0.35,
            "annual_return": 0.35,
        }
        result = diagnose(metrics)
        assert len(result["strengths"]) > 0
        assert len(result["weaknesses"]) == 0

    def test_bad_strategy(self):
        metrics = {
            "win_rate": 0.3,
            "profit_loss_ratio": 0.8,
            "max_drawdown": -0.35,
            "sharpe_ratio": 0.3,
            "total_trades": 3,
            "winning_trades": 1,
            "losing_trades": 2,
            "avg_holding_days": 10,
            "max_consecutive_wins": 1,
            "max_consecutive_losses": 2,
            "total_return": -0.1,
            "annual_return": -0.1,
        }
        result = diagnose(metrics)
        assert len(result["weaknesses"]) > 0
        assert len(result["suggestions"]) > 0
