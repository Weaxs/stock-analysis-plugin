from tools.backtest import diagnose_advanced


def _metrics(**kw):
    base = {
        "total_trades": 10,
        "win_rate": 0.5,
        "profit_loss_ratio": 1.5,
        "max_drawdown": -0.1,
        "total_return": 0.15,
    }
    base.update(kw)
    return base


def _strategy(stop_loss=-0.05, take_profit=0.30):
    return {"exit": {"stop_loss": stop_loss, "take_profit": take_profit}}


class TestTradeCountQuality:
    def test_too_low(self):
        d = diagnose_advanced(_metrics(total_trades=3), [], [], _strategy())
        assert d["trade_count_quality"] == "too_low"

    def test_marginal(self):
        d = diagnose_advanced(_metrics(total_trades=10), [], [], _strategy())
        assert d["trade_count_quality"] == "marginal"

    def test_sufficient(self):
        d = diagnose_advanced(_metrics(total_trades=20), [], [], _strategy())
        assert d["trade_count_quality"] == "sufficient"


class TestFailureReason:
    def test_stop_loss_heavy(self):
        trades = [{"action": "sell", "pnl": -100, "pnl_pct": -6.0, "reason": "stop_loss"}] * 5
        d = diagnose_advanced(_metrics(total_return=-0.1, total_trades=5), trades, [], _strategy(stop_loss=-0.05))
        assert "止损" in d["main_failure_reason"]

    def test_low_win_rate(self):
        trades = [{"action": "sell", "pnl": -100, "pnl_pct": -3.0, "reason": "exit"}] * 5
        d = diagnose_advanced(_metrics(total_return=-0.1, win_rate=0.2, total_trades=5), trades, [], _strategy())
        assert "胜率" in d["main_failure_reason"]

    def test_profitable_no_failure(self):
        d = diagnose_advanced(_metrics(total_return=0.2, max_drawdown=-0.1), [], [], _strategy())
        assert d["main_failure_reason"] is None

    def test_high_drawdown_flagged_even_when_profitable(self):
        d = diagnose_advanced(_metrics(total_return=0.2, max_drawdown=-0.3), [], [], _strategy())
        assert d["main_failure_reason"] is not None
        assert "回撤" in d["main_failure_reason"]


class TestRegimeFit:
    def test_early_period_better(self):
        # equity rises then falls
        curve = [[0, 100000], [10, 120000], [20, 110000]]
        d = diagnose_advanced(_metrics(), [], curve, _strategy())
        assert d["regime_fit"] == "better_in_early_period"

    def test_late_period_better(self):
        curve = [[0, 100000], [10, 95000], [20, 115000]]
        d = diagnose_advanced(_metrics(), [], curve, _strategy())
        assert d["regime_fit"] == "better_in_late_period"

    def test_consistent(self):
        curve = [[0, 100000], [10, 108000], [20, 118000]]
        d = diagnose_advanced(_metrics(), [], curve, _strategy())
        assert d["regime_fit"] == "consistent_across_periods"

    def test_short_curve_unknown(self):
        d = diagnose_advanced(_metrics(), [], [], _strategy())
        assert d["regime_fit"] == "unknown"


class TestSuggestedParameterChanges:
    def test_loosen_stop_loss(self):
        trades = [{"action": "sell", "pnl_pct": -6.0, "reason": "stop_loss"}] * 5
        d = diagnose_advanced(_metrics(total_return=-0.1, total_trades=5), trades, [], _strategy(stop_loss=-0.05))
        params = [s["parameter"] for s in d["suggested_parameter_changes"]]
        assert "exit.stop_loss" in params

    def test_raise_take_profit(self):
        # win rate ok, profit/loss ratio too low
        trades = [{"action": "sell", "pnl_pct": 2.0, "reason": "take_profit"}] * 5
        d = diagnose_advanced(
            _metrics(win_rate=0.55, profit_loss_ratio=0.8, total_trades=10),
            trades,
            [],
            _strategy(take_profit=0.10),
        )
        params = [s["parameter"] for s in d["suggested_parameter_changes"]]
        assert "exit.take_profit" in params

    def test_reduce_position_on_drawdown(self):
        d = diagnose_advanced(_metrics(max_drawdown=-0.3), [], [], _strategy())
        params = [s["parameter"] for s in d["suggested_parameter_changes"]]
        assert "position.size" in params


if __name__ == "__main__":
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
