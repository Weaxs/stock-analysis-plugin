import pytest

from tools import alert_rules


@pytest.mark.parametrize(
    "bad_rule",
    [
        {"type": "price_above", "value": "bad"},
        {"type": "price_above"},
        {"type": ["price_above"], "value": 10},
        {"type": "price_above", "value": float("nan")},
        {"type": "risk_level_at_least", "value": ["high"]},
        {"type": "risk_level_at_least", "value": "critical"},
        {"type": "anomaly", "value": ""},
    ],
)
def test_bad_rules_do_not_crash_or_disable_valid_siblings(monkeypatch, bad_rule):
    monkeypatch.setattr(
        alert_rules,
        "_run_json",
        lambda script, *a, **kw: {
            "stock_data.py": QUOTE,
            "risk_screening.py": RISK,
            "anomaly_detect.py": ANOM,
        }.get(script),
    )
    result = alert_rules.check_rules("AAPL", [bad_rule, {"type": "price_above", "value": 90}])
    assert "error" in result["evaluated"][0]
    assert result["evaluated"][1]["triggered"] is True


QUOTE = {"price": 100, "change_pct": 2.5}
TECH = {"volume": {"volume_ratio": 3.2}}
ANOM = {"anomalies": [{"type": "macd_golden_cross", "severity": "high", "description": "MACD金叉"}]}
RISK = {"veto_buy": True, "risk_level": "high"}


class TestCheckRule:
    def test_price_below_triggers(self):
        r = alert_rules._check_rule({"type": "price_below", "value": 110}, QUOTE, TECH, ANOM, RISK)
        assert r["triggered"] is True
        assert r["actual"] == 100

    def test_price_below_not_triggered(self):
        r = alert_rules._check_rule({"type": "price_below", "value": 90}, QUOTE, TECH, ANOM, RISK)
        assert r is None

    def test_price_above_triggers(self):
        r = alert_rules._check_rule({"type": "price_above", "value": 90}, QUOTE, TECH, ANOM, RISK)
        assert r["triggered"] is True

    def test_change_pct_above(self):
        r = alert_rules._check_rule({"type": "change_pct_above", "value": 2}, QUOTE, TECH, ANOM, RISK)
        assert r["triggered"] is True

    def test_change_pct_below(self):
        q = {"price": 100, "change_pct": -3}
        r = alert_rules._check_rule({"type": "change_pct_below", "value": -2}, q, TECH, ANOM, RISK)
        assert r["triggered"] is True

    def test_volume_ratio_above(self):
        r = alert_rules._check_rule({"type": "volume_ratio_above", "value": 2}, QUOTE, TECH, ANOM, RISK)
        assert r["triggered"] is True
        assert r["actual"] == 3.2

    def test_anomaly_type_match(self):
        r = alert_rules._check_rule({"type": "anomaly", "value": "macd_golden_cross"}, QUOTE, TECH, ANOM, RISK)
        assert r["triggered"] is True
        assert r["severity"] == "high"

    def test_anomaly_type_no_match(self):
        r = alert_rules._check_rule({"type": "anomaly", "value": "rsi_oversold"}, QUOTE, TECH, ANOM, RISK)
        assert r is None

    def test_risk_veto(self):
        r = alert_rules._check_rule({"type": "risk_veto"}, QUOTE, TECH, ANOM, RISK)
        assert r["triggered"] is True
        assert r["severity"] == "high"

    def test_risk_level_at_least(self):
        r = alert_rules._check_rule({"type": "risk_level_at_least", "value": "medium"}, QUOTE, TECH, ANOM, RISK)
        assert r["triggered"] is True

    def test_unknown_rule_type(self):
        r = alert_rules._check_rule({"type": "bogus", "value": 1}, QUOTE, TECH, ANOM, RISK)
        assert r["triggered"] is False
        assert "error" in r


class TestCheckRules:
    def test_error_sources_are_unavailable_not_negative_results(self, monkeypatch):
        monkeypatch.setattr(
            alert_rules, "_run_json", lambda script, *a, **kw: QUOTE if script == "stock_data.py" else {"error": "down"}
        )
        result = alert_rules.check_rules(
            "AAPL",
            [
                {"type": "price_below", "value": 110},
                {"type": "volume_ratio_above", "value": 2},
                {"type": "anomaly", "value": "macd_golden_cross"},
                {"type": "risk_veto"},
            ],
        )
        assert result["hits"][0]["rule"] == "price_below"
        assert all("error" in item for item in result["evaluated"][1:])
        assert len(result["meta"]["warnings"]) == 3

    def test_only_fetches_needed(self, monkeypatch):
        calls = []

        def fake_run(script, args, **kw):
            calls.append(script)
            return {"price": 100, "change_pct": 1}

        monkeypatch.setattr(alert_rules, "_run_json", fake_run)
        # only price rule → should NOT fetch technical/anomaly/risk
        result = alert_rules.check_rules("600519", [{"type": "price_below", "value": 200}])
        assert "stock_data.py" in calls
        assert "technical.py" not in calls
        assert "anomaly_detect.py" not in calls
        assert "risk_screening.py" not in calls
        assert result["triggered"] is True

    def test_multiple_rules_aggregated(self, monkeypatch):
        def fake_run(script, args, **kw):
            if script == "stock_data.py":
                return {"price": 100, "change_pct": 5}
            if script == "risk_screening.py":
                return {"veto_buy": True, "risk_level": "high"}
            return None

        monkeypatch.setattr(alert_rules, "_run_json", fake_run)
        result = alert_rules.check_rules(
            "600519",
            [
                {"type": "price_below", "value": 200},
                {"type": "change_pct_above", "value": 3},
                {"type": "risk_veto"},
            ],
        )
        assert result["triggered"] is True
        assert result["hit_count"] == 3

    def test_no_rules_error(self):
        result = alert_rules.check_rules("600519", [])
        assert "error" in result

    def test_fetches_run_concurrently(self, monkeypatch):
        """quote/tech/anom/risk are independent subprocesses — they must fan out in
        parallel (the barrier only releases when all four are in flight at once;
        a serial chain would time it out)."""
        import threading

        barrier = threading.Barrier(4, timeout=10)

        def fake_run(script, args, **kw):
            barrier.wait()
            return {"price": 100}

        monkeypatch.setattr(alert_rules, "_run_json", fake_run)
        result = alert_rules.check_rules(
            "600519",
            [
                {"type": "price_below", "value": 200},
                {"type": "volume_ratio_above", "value": 1},
                {"type": "anomaly", "value": "macd_golden_cross"},
                {"type": "risk_veto"},
            ],
        )
        assert result["symbol"] == "600519"
        assert result["triggered"] is True  # price_below fired
        assert len(result["evaluated"]) == 4

    def test_evaluated_order_matches_rule_order(self, monkeypatch):
        """Parallel fetch must not change output structure: evaluated/hits follow
        the input rules' order."""
        monkeypatch.setattr(
            alert_rules,
            "_run_json",
            lambda script, args, **kw: {"price": 100, "change_pct": 5} if script == "stock_data.py" else None,
        )
        result = alert_rules.check_rules(
            "600519",
            [
                {"type": "change_pct_above", "value": 3},
                {"type": "price_below", "value": 200},
            ],
        )
        assert [e["rule"] for e in result["evaluated"]] == ["change_pct_above", "price_below"]


if __name__ == "__main__":
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
