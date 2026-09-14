import subprocess
import sys
from unittest.mock import MagicMock, patch

from tools._subproc import run_tool


class TestRunToolArgv:
    """run_tool spawns an argv list with no shell: user-controlled symbols/queries
    arrive as one literal list element, never interpolated into a shell command.
    (Converged from the per-tool TestRunToolArgv copies when the four _run_tool
    variants were extracted into tools/_subproc.py.)"""

    def test_args_passed_as_argv_list(self):
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}")
            result = run_tool("stock_data.py", ["quote", "600519"])
        cmd = mock_run.call_args[0][0]
        assert isinstance(cmd, list)
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("stock_data.py")
        assert cmd[2:] == ["quote", "600519"]
        assert not mock_run.call_args.kwargs.get("shell")
        assert result == {}

    def test_malicious_symbol_stays_single_element(self):
        evil = '600519"; rm -rf / #'
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = run_tool("stock_data.py", ["quote", evil])
        cmd = mock_run.call_args[0][0]
        assert cmd.count(evil) == 1
        assert not mock_run.call_args.kwargs.get("shell")
        assert result is None


class TestRunToolFailureSentinel:
    """The unified failure contract is None (the four copies disagreed: two
    returned {}, two returned None) — callers pick their own `or {}`/`or []`."""

    def test_nonzero_exit_returns_none(self):
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=2, stdout="{}")
            assert run_tool("stock_data.py", ["quote", "600519"]) is None

    def test_invalid_json_returns_none(self):
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json")
            assert run_tool("stock_data.py", ["quote", "600519"]) is None

    def test_timeout_returns_none(self):
        with patch(
            "tools._subproc.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30),
        ):
            assert run_tool("stock_data.py", ["quote", "600519"]) is None
