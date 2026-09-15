import subprocess
import sys
from unittest.mock import MagicMock, patch

from tools._subproc import find_python, run_tool


class TestRunToolArgv:
    """run_tool spawns an argv list with no shell: user-controlled symbols/queries
    arrive as one literal list element, never interpolated into a shell command."""

    def test_args_passed_as_argv_list(self):
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}")
            result = run_tool("stock_data.py", ["quote", "600519"])
        cmd = mock_run.call_args[0][0]
        assert isinstance(cmd, list)
        assert cmd[0] == find_python()
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

    def test_timeout_passed_through(self):
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}")
            run_tool("stock_data.py", ["quote", "600519"], timeout=60)
        assert mock_run.call_args.kwargs["timeout"] == 60


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


class TestRunToolRawStdout:
    """parse_json=False serves gather.py's contract: stripped raw stdout, leaving
    JSON parsing to the caller."""

    def test_non_json_stdout_returned_verbatim(self):
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json\n")
            assert run_tool("stock_data.py", ["quote", "600519"], parse_json=False) == "not json"

    def test_json_stdout_stays_string(self):
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"a": 1}\n')
            assert run_tool("stock_data.py", ["quote", "600519"], parse_json=False) == '{"a": 1}'

    def test_failure_still_returns_none(self):
        with patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="boom")
            assert run_tool("stock_data.py", ["quote", "600519"], parse_json=False) is None


class TestFindPython:
    def test_venv_python_preferred_when_present(self, tmp_path, monkeypatch):
        venv = tmp_path / ".venv" / "bin" / "python3"
        venv.parent.mkdir(parents=True)
        venv.touch()
        monkeypatch.setattr("tools._subproc.TOOLS_DIR", str(tmp_path / "tools"))
        assert find_python() == str(venv)

    def test_falls_back_to_sys_executable(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tools._subproc.TOOLS_DIR", str(tmp_path / "tools"))
        assert find_python() == sys.executable
