import sys
from unittest.mock import MagicMock, patch

from tools.risk_screening import _run_tool, _safe_float, compute_risk


class TestSafeFloat:
    def test_normal_float(self):
        assert _safe_float(3.14) == 3.14

    def test_string_number(self):
        assert _safe_float("42.5") == 42.5

    def test_none(self):
        assert _safe_float(None) is None

    def test_nan(self):
        assert _safe_float(float("nan")) is None

    def test_non_numeric_string(self):
        assert _safe_float("abc") is None

    def test_int(self):
        assert _safe_float(10) == 10.0

    def test_zero(self):
        assert _safe_float(0) == 0.0


class TestComputeRisk:
    def test_empty_flags(self):
        result = compute_risk([])
        assert result["risk_score"] == 0
        assert result["risk_level"] == "low"
        assert result["veto_buy"] is False

    def test_low_risk(self):
        flags = [{"severity": "low"}, {"severity": "low"}]
        result = compute_risk(flags)
        assert result["risk_score"] == 10
        assert result["risk_level"] == "low"
        assert result["veto_buy"] is False

    def test_medium_risk(self):
        flags = [{"severity": "medium"}, {"severity": "medium"}]
        result = compute_risk(flags)
        assert result["risk_score"] == 30
        assert result["risk_level"] == "medium"
        assert result["veto_buy"] is False

    def test_high_risk(self):
        flags = [{"severity": "high"}, {"severity": "high"}]
        result = compute_risk(flags)
        assert result["risk_score"] == 60
        assert result["risk_level"] == "high"
        assert result["veto_buy"] is True

    def test_cap_at_100(self):
        flags = [{"severity": "high"}] * 5
        result = compute_risk(flags)
        assert result["risk_score"] == 100
        assert result["risk_level"] == "high"

    def test_mixed_severity(self):
        flags = [
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
        ]
        result = compute_risk(flags)
        assert result["risk_score"] == 50
        assert result["risk_level"] == "medium"
        assert result["veto_buy"] is True

    def test_veto_requires_high(self):
        flags = [{"severity": "medium"}] * 4
        result = compute_risk(flags)
        assert result["risk_score"] == 60
        assert result["veto_buy"] is False


class TestRunToolArgv:
    """_run_tool spawns an argv list with no shell: user-controlled symbols/queries
    arrive as one literal list element, never interpolated into a shell command."""

    def test_args_passed_as_argv_list(self):
        with patch("tools.risk_screening.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}")
            result = _run_tool("stock_data.py", ["quote", "600519"])
        cmd = mock_run.call_args[0][0]
        assert isinstance(cmd, list)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("stock_data.py")
        assert cmd[2:] == ["quote", "600519"]
        assert not mock_run.call_args.kwargs.get("shell")
        assert result == {}

    def test_malicious_symbol_stays_single_element(self):
        evil = '600519"; rm -rf / #'
        with patch("tools.risk_screening.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            _run_tool("stock_data.py", ["quote", evil])
        cmd = mock_run.call_args[0][0]
        assert cmd.count(evil) == 1
        assert not mock_run.call_args.kwargs.get("shell")
