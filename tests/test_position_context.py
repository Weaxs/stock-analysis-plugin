import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import position_context


def test_cli_invalid_position_exits_nonzero():
    script = Path(__file__).resolve().parents[1] / "tools" / "position_context.py"
    result = subprocess.run(
        [sys.executable, str(script), "analyze", "AAPL", "--cost", "0", "--quantity", "1"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "error" in json.loads(result.stdout)
    assert result.returncode == 1


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), "bad"])
def test_invalid_quote_is_clean_error(monkeypatch, price):
    monkeypatch.setattr(position_context, "_run_json", lambda *a, **kw: {"price": price})
    assert "error" in position_context.analyze_position("AAPL", cost=100, quantity=10)


@pytest.mark.parametrize("overrides", [{"cost": 0}, {"quantity": -1}, {"stop_loss": float("nan")}])
def test_invalid_position_is_clean_error(monkeypatch, overrides):
    monkeypatch.setattr(position_context, "_run_json", lambda *a, **kw: {"price": 100})
    inputs = {"cost": 100, "quantity": 10, **overrides}
    assert "error" in position_context.analyze_position("AAPL", **inputs)


class TestRiskLevel:
    def test_near_stop_is_high(self):
        assert position_context._risk_level(2.0, 5.0, "bullish") == "high"

    def test_deep_loss_is_high(self):
        assert position_context._risk_level(10.0, -8.0, "neutral") == "high"

    def test_bearish_trend_with_loss_is_high(self):
        assert position_context._risk_level(10.0, -2.0, "bearish") == "high"

    def test_medium_case(self):
        assert position_context._risk_level(4.0, -1.0, "neutral") == "medium"

    def test_low_case(self):
        assert position_context._risk_level(15.0, 8.0, "bullish") == "low"


class TestAdvice:
    def test_near_stop_advice(self):
        assert "止损" in position_context._advice(5.0, 2.0, None, "bullish", 90, 100)

    def test_near_take_profit(self):
        # pnl>15, distance_to_take<5
        assert "止盈" in position_context._advice(16.0, 20.0, 3.0, "bullish", 90, 100)

    def test_move_stop_to_cost(self):
        # pnl>8, stop below cost
        assert "上移" in position_context._advice(10.0, 20.0, None, "bullish", 90, 100)


class TestAnalyzePosition:
    def test_pnl_computation(self, monkeypatch):
        def fake_run(script, args, **kw):
            if script == "stock_data.py":
                return {"name": "Test", "price": 110}
            if script == "technical.py":
                return {
                    "trend": {"overall": "bullish"},
                    "support_resistance": {"support": 105, "resistance": 120},
                }
            return None

        monkeypatch.setattr(position_context, "_run_json", fake_run)
        result = position_context.analyze_position("600519", cost=100, quantity=100, stop_loss=95, take_profit=130)
        assert result["position"]["pnl_pct"] == 10.0
        assert result["position"]["total_pnl"] == 1000.0
        assert result["position"]["market_value"] == 11000.0
        assert result["levels"]["stop_loss"] == 95
        # (110-95)/110 ≈ 13.64
        assert abs(result["levels"]["distance_to_stop_loss_pct"] - 13.64) < 0.1
        assert result["trend"] == "bullish"
        assert result["risk_level"] in ("low", "medium", "high")

    def test_missing_price_returns_error(self, monkeypatch):
        monkeypatch.setattr(position_context, "_run_json", lambda *a, **kw: {})
        result = position_context.analyze_position("XXX", cost=100, quantity=10)
        assert "error" in result


if __name__ == "__main__":
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
