import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from tools._subproc import find_python, run_tool


@pytest.mark.parametrize(
    "module,handler,argv",
    [
        ("backtest", "run_backtest", ["run", "unused", "AAPL"]),
        ("alert_rules", "check_rules", ["check", "AAPL", "--rules", "[]"]),
        ("report_renderer", "render", ["stock", "--input-b64", "e30="]),
        ("market_regime", "detect_regime", ["detect", "US"]),
        ("stock_data", "cmd_quote", ["quote", "AAPL"]),
    ],
)
def test_cli_errors_have_nonzero_status(monkeypatch, capsys, module, handler, argv):
    import importlib
    import json

    tool = importlib.import_module(f"tools.{module}")
    monkeypatch.setattr(tool, handler, lambda *a, **kw: {"error": "unavailable"})
    monkeypatch.setattr(sys, "argv", [module + ".py", *argv])
    with pytest.raises(SystemExit) as exc:
        tool.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out) == {"error": "unavailable"}


class TestRunToolArgv:
    """run_tool spawns an argv list with no shell: user-controlled symbols/queries
    arrive as one literal list element, never interpolated into a shell command."""

    def test_args_passed_as_argv_list(self):
        # 接线语义：cmd[0] 必须是 find_python() 的返回值 —— 哨兵值钉住这条接线，
        # 而不是断言 run_tool 与自身比较（恒真）。
        sentinel = patch("tools._subproc.find_python", return_value="/sentinel/python")
        with sentinel, patch("tools._subproc.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}")
            result = run_tool("stock_data.py", ["quote", "600519"])
        cmd = mock_run.call_args[0][0]
        assert isinstance(cmd, list)
        assert cmd[0] == "/sentinel/python"
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


class TestRunToolRawStdout:
    """parse_json=False serves gather.py's contract: stripped raw stdout, leaving
    JSON parsing to the caller."""

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

    def test_windows_venv_layout_preferred_when_present(self, tmp_path, monkeypatch):
        # Windows venvs ship Scripts/python.exe, not bin/python3
        monkeypatch.setattr("tools._subproc.sys.platform", "win32")
        venv = tmp_path / ".venv" / "Scripts" / "python.exe"
        venv.parent.mkdir(parents=True)
        venv.touch()
        monkeypatch.setattr("tools._subproc.TOOLS_DIR", str(tmp_path / "tools"))
        assert find_python() == str(venv)


@pytest.mark.parametrize("parse_json", [True, False])
def test_preserve_structured_failure_without_leaking_key(parse_json):
    import json

    with patch("tools._subproc.subprocess.run") as call:
        call.return_value = MagicMock(returncode=1, stdout='{"error":"unavailable?token=private"}')
        result = run_tool("stock_data.py", ["quote", "AAPL"], parse_json=parse_json, preserve_errors=True)
    payload = result if parse_json else json.loads(result)
    assert payload["error"] == "unavailable?token=***"


@pytest.mark.parametrize("stdout,code", [("", "empty_output"), ("oops", "invalid_json")])
def test_preserve_invalid_output_reason(stdout, code):
    with patch("tools._subproc.subprocess.run", return_value=MagicMock(returncode=0, stdout=stdout)):
        assert run_tool("stock_data.py", ["quote", "AAPL"], preserve_errors=True)["error_code"] == code


def test_preserve_timeout_reason():
    with patch("tools._subproc.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="secret", timeout=2)):
        assert run_tool("stock_data.py", ["quote", "AAPL"], timeout=2, preserve_errors=True) == {
            "error": "Command timed out after 2s",
            "error_code": "timeout",
        }


def test_nested_screen_budgets_leave_time_to_finish():
    from tools._subproc import command_timeout

    assert command_timeout("stock_data.py", ["market_snapshot"]) == 240
    assert command_timeout("screener.py", ["screen"]) == 1830
    assert command_timeout("sec_edgar.py", ["holdings"]) == 300
    assert command_timeout("stock_data.py", ["quote"]) == 120


def test_default_subprocess_budget_comes_from_command_table():
    with patch("tools._subproc.subprocess.run", return_value=MagicMock(returncode=0, stdout="{}")) as call:
        run_tool("stock_data.py", ["quote", "600519"])
        assert call.call_args.kwargs["timeout"] == 120
        run_tool("stock_data.py", ["market_snapshot", "--market", "A"])
        assert call.call_args.kwargs["timeout"] == 240


def test_composite_commands_have_room_for_nested_collectors():
    from tools._subproc import command_timeout

    leaf = command_timeout("stock_data.py", ["kline"])
    technical = command_timeout("technical.py", ["analyze"])
    assert technical > leaf
    assert command_timeout("market_regime.py", ["detect"]) > 2 * leaf
    assert command_timeout("backtest.py", ["run"]) > 2 * leaf
    assert command_timeout("risk_screening.py", ["screen"]) > leaf + technical + 25 + leaf


@pytest.mark.parametrize("count,workers", [(1, 3), (7, 3), (20, 1)])
def test_batch_budgets_cover_all_worker_waves(count, workers):
    from tools._subproc import command_timeout

    symbols = ",".join(["600519"] * count)
    waves = (count + workers - 1) // workers
    argv = ["analyze", symbols, "--workers", str(workers)]
    assert command_timeout("watchlist.py", argv) > waves * command_timeout("risk_screening.py", ["screen"])
    context = command_timeout("watchlist_context.py", ["build", *argv[1:], "--include-market-review"])
    anomaly_waves = (count + min(workers * 2, 6) - 1) // min(workers * 2, 6)
    assert context > command_timeout("watchlist.py", argv) + anomaly_waves * 390 + 3 * 300


@pytest.mark.parametrize("top", [1, 20, 100])
def test_screen_budget_covers_sequential_enrichment_waves(top):
    from tools._subproc import command_timeout

    waves = (top + 5) // 6
    assert command_timeout("screener.py", ["screen", "--top", str(top)]) > 240 + 120 + waves * 3 * 120


def test_all_market_reviews_cover_three_sequential_markets():
    from tools._subproc import command_timeout

    single = command_timeout("market_review.py", ["review"])
    assert single > command_timeout("market_regime.py", ["detect"])
    assert command_timeout("market_review.py", ["review", "--market", "all"]) > 3 * single


def test_python_and_host_batch_budgets_match():
    import json
    from pathlib import Path

    from tools._subproc import command_timeout

    cases = (
        [["screener.py", ["screen", "--top", str(top)]] for top in [1, 20, 100]]
        + [
            [script, [command, "AAPL,600519,0700.HK,,MSFT", "--workers", str(workers), "--include-market-review"]]
            for script, command in [("watchlist.py", "analyze"), ("watchlist_context.py", "build")]
            for workers in [1, 3, 10]
        ]
        + [
            ["market_review.py", ["review", "--market", "all"]],
            ["technical.py", ["analyze", "AAPL", "--periods", "daily,weekly,monthly"]],
            ["technical.py", ["analyze", "AAPL", "--periods", "weekly"]],
            ["technical.py", ["analyze", "AAPL", "--period", "monthly", "--periods", "daily,weekly"]],
            ["screener.py", ["screen", "--top", "invalid"]],
            ["watchlist.py", ["analyze", "", "--workers", "0"]],
        ]
    )
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            'import {commandTimeout} from "./scripts/cli-timeout.mjs";'
            "process.stdout.write(JSON.stringify(JSON.parse(process.argv[1]).map(([script,args]) => commandTimeout(script,args))));",
            json.dumps(cases),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [command_timeout(script, argv) * 1000 + 30000 for script, argv in cases]


def test_alert_budget_covers_risk_and_anomaly_children():
    from tools._subproc import command_timeout

    assert command_timeout("alert_rules.py", ["check"]) > max(
        command_timeout("risk_screening.py", ["screen"]), command_timeout("anomaly_detect.py", ["detect"])
    )


@pytest.mark.parametrize(
    "argv,count",
    [
        (["analyze", "AAPL", "--periods", "weekly"], 2),
        (["analyze", "AAPL", "--period", "monthly", "--periods", "daily,weekly"], 3),
        (["analyze", "AAPL", "--periods", "daily,daily"], 1),
    ],
)
def test_technical_budget_includes_primary_period(argv, count):
    from tools._subproc import command_timeout

    assert command_timeout("technical.py", argv) >= count * 150
