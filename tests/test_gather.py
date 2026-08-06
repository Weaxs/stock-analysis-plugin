import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tools.gather import (
    TOOLS_DIR,
    _find_python,
    _parse_json,
    _run,
    gather_analysis,
    gather_fundamental,
    gather_screen,
    gather_technical,
)


class TestParseJson:
    def test_none_returns_none(self):
        assert _parse_json(None) is None

    def test_valid_json_object(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_valid_json_array(self):
        assert _parse_json("[1, 2, 3]") == [1, 2, 3]

    def test_invalid_json_returns_raw_string(self):
        assert _parse_json("not json at all") == "not json at all"

    def test_empty_string(self):
        assert _parse_json("") == ""


class TestFindPython:
    @patch("tools.gather.TOOLS_DIR")
    def test_uses_venv_when_exists(self, mock_tools_dir):
        mock_venv = MagicMock()
        mock_venv.exists.return_value = True
        mock_venv.__str__ = lambda self: "/project/.venv/bin/python3"
        mock_tools_dir.parent.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value = mock_venv
        result = _find_python()
        assert isinstance(result, str)

    def test_returns_string(self):
        result = _find_python()
        assert isinstance(result, str)
        assert len(result) > 0


class TestRun:
    @patch("tools.gather.subprocess.run")
    @patch("tools.gather._find_python", return_value="/usr/bin/python3")
    def test_success(self, mock_python, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"ok": true}\n')
        result = _run("stock_data.py", ["quote", "600519"])
        assert result == '{"ok": true}'
        mock_run.assert_called_once_with(
            ["/usr/bin/python3", str(TOOLS_DIR / "stock_data.py"), "quote", "600519"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    @patch("tools.gather.subprocess.run")
    @patch("tools.gather._find_python", return_value="/usr/bin/python3")
    def test_nonzero_returncode(self, mock_python, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="error output")
        result = _run("stock_data.py", ["quote", "INVALID"])
        assert result is None

    @patch("tools.gather.subprocess.run")
    @patch("tools.gather._find_python", return_value="/usr/bin/python3")
    def test_empty_stdout(self, mock_python, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n")
        result = _run("stock_data.py", ["quote", "600519"])
        assert result is None

    @patch("tools.gather.subprocess.run")
    @patch("tools.gather._find_python", return_value="/usr/bin/python3")
    def test_timeout(self, mock_python, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=60)
        result = _run("stock_data.py", ["quote", "600519"])
        assert result is None

    @patch("tools.gather.subprocess.run")
    @patch("tools.gather._find_python", return_value="/usr/bin/python3")
    def test_exception(self, mock_python, mock_run):
        mock_run.side_effect = OSError("No such file")
        result = _run("stock_data.py", ["quote", "600519"])
        assert result is None

    @patch("tools.gather.subprocess.run")
    @patch("tools.gather._find_python", return_value="/usr/bin/python3")
    def test_custom_timeout(self, mock_python, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="data")
        _run("screener.py", ["screen"], timeout=120)
        mock_run.assert_called_once_with(
            ["/usr/bin/python3", str(TOOLS_DIR / "screener.py"), "screen"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )


class TestGatherAnalysis:
    @patch("tools.gather._run")
    def test_returns_all_keys(self, mock_run):
        mock_run.return_value = '{"data": "value"}'
        result = gather_analysis("600519")
        expected_keys = {"quote", "kline", "technical", "financials", "capital_flow", "news", "risk", "regime"}
        assert set(result.keys()) == expected_keys

    @patch("tools.gather._run")
    def test_correct_args_for_quote(self, mock_run):
        mock_run.return_value = None
        gather_analysis("AAPL")
        calls = mock_run.call_args_list
        quote_call = [c for c in calls if c[0][0] == "stock_data.py" and "quote" in c[0][1]]
        assert len(quote_call) == 1
        assert quote_call[0][0] == ("stock_data.py", ["quote", "AAPL"])

    @patch("tools.gather._run")
    def test_correct_args_for_kline(self, mock_run):
        mock_run.return_value = None
        gather_analysis("600519")
        calls = mock_run.call_args_list
        kline_call = [c for c in calls if c[0][0] == "stock_data.py" and "kline" in c[0][1]]
        assert len(kline_call) == 1
        assert kline_call[0][0] == ("stock_data.py", ["kline", "600519", "--period", "daily", "--count", "120"])

    @patch("tools.gather._run")
    def test_handles_partial_failure(self, mock_run):
        def side_effect(script, args, **kwargs):
            if "quote" in args:
                return '{"price": 100}'
            return None

        mock_run.side_effect = side_effect
        result = gather_analysis("600519")
        assert result["quote"] == {"price": 100}
        assert result["regime"] is None

    @patch("tools.gather._run")
    def test_parses_json_results(self, mock_run):
        mock_run.return_value = '{"score": 85}'
        result = gather_analysis("600519")
        for v in result.values():
            assert v == {"score": 85}


class TestGatherTechnical:
    @patch("tools.gather._run")
    def test_default_params(self, mock_run):
        mock_run.return_value = '{"data": 1}'
        result = gather_technical("AAPL")
        assert "kline" in result
        assert "technical" in result
        assert "quote" not in result

    @patch("tools.gather._run")
    def test_with_quote(self, mock_run):
        mock_run.return_value = '{"data": 1}'
        result = gather_technical("AAPL", with_quote=True)
        assert "quote" in result

    @patch("tools.gather._run")
    def test_custom_kline_count(self, mock_run):
        mock_run.return_value = None
        gather_technical("600519", kline_count=30)
        calls = mock_run.call_args_list
        kline_call = [c for c in calls if c[0][0] == "stock_data.py" and "kline" in c[0][1]]
        assert "--count" in kline_call[0][0][1]
        count_idx = kline_call[0][0][1].index("--count")
        assert kline_call[0][0][1][count_idx + 1] == "30"

    @patch("tools.gather._run")
    def test_kline_count_90(self, mock_run):
        mock_run.return_value = None
        gather_technical("AAPL", kline_count=90)
        calls = mock_run.call_args_list
        technical_call = [c for c in calls if c[0][0] == "technical.py"]
        assert "--count" in technical_call[0][0][1]
        count_idx = technical_call[0][0][1].index("--count")
        assert technical_call[0][0][1][count_idx + 1] == "90"


class TestGatherScreen:
    @patch("tools.gather._run")
    def test_default_params(self, mock_run):
        mock_run.return_value = '[{"code": "600519"}]'
        result = gather_screen()
        mock_run.assert_called_once_with("screener.py", ["screen", "--market", "A", "--top", "20"], timeout=120)
        assert result == {"screen_results": [{"code": "600519"}]}

    @patch("tools.gather._run")
    def test_custom_params(self, mock_run):
        mock_run.return_value = "[]"
        gather_screen(market="US", top=10, config="/path/to/config.json")
        mock_run.assert_called_once_with(
            "screener.py",
            ["screen", "--market", "US", "--top", "10", "--config", "/path/to/config.json"],
            timeout=120,
        )

    @patch("tools.gather._run")
    def test_none_result(self, mock_run):
        mock_run.return_value = None
        result = gather_screen()
        assert result == {"screen_results": None}


class TestMain:
    @patch("tools.gather.gather_analysis")
    def test_analysis_command(self, mock_gather, capsys):
        mock_gather.return_value = {"quote": {"price": 100}}
        import tools.gather

        with patch("sys.argv", ["gather.py", "analysis", "600519"]):
            tools.gather.main()
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data == {"quote": {"price": 100}}

    @patch("tools.gather.gather_technical")
    def test_technical_command(self, mock_gather, capsys):
        mock_gather.return_value = {"kline": [1, 2, 3]}
        import tools.gather

        with patch("sys.argv", ["gather.py", "technical", "AAPL", "--kline-count", "90", "--with-quote"]):
            tools.gather.main()
        mock_gather.assert_called_once_with("AAPL", 90, True)

    @patch("tools.gather.gather_screen")
    def test_screen_command(self, mock_gather, capsys):
        mock_gather.return_value = {"screen_results": []}
        import tools.gather

        with patch("sys.argv", ["gather.py", "screen", "--market", "US", "--top", "10"]):
            tools.gather.main()
        mock_gather.assert_called_once_with("US", 10, None)

    def test_no_command_exits(self):
        import tools.gather

        with patch("sys.argv", ["gather.py"]):
            with pytest.raises(SystemExit) as exc_info:
                tools.gather.main()
            assert exc_info.value.code == 1


class TestGatherFundamental:
    @patch("tools.gather._run")
    def test_returns_all_keys(self, mock_run):
        mock_run.return_value = '{"data": "value"}'
        result = gather_fundamental("600519")
        expected_keys = {"quote", "kline", "technical", "financials", "news", "stock_info", "sector_rankings"}
        assert set(result.keys()) == expected_keys

    @patch("tools.gather._run")
    def test_correct_kline_count(self, mock_run):
        mock_run.return_value = None
        gather_fundamental("AAPL")
        calls = mock_run.call_args_list
        kline_call = [c for c in calls if c[0][0] == "stock_data.py" and "kline" in c[0][1]]
        assert len(kline_call) == 1
        assert kline_call[0][0] == ("stock_data.py", ["kline", "AAPL", "--period", "daily", "--count", "60"])

    @patch("tools.gather._run")
    def test_handles_partial_failure(self, mock_run):
        def side_effect(script, args, **kwargs):
            if "quote" in args:
                return '{"price": 100}'
            return None

        mock_run.side_effect = side_effect
        result = gather_fundamental("600519")
        assert result["quote"] == {"price": 100}
        assert result["sector_rankings"] is None

    @patch("tools.gather._run")
    def test_sector_rankings_args(self, mock_run):
        mock_run.return_value = None
        gather_fundamental("600519")
        calls = mock_run.call_args_list
        sector_call = [c for c in calls if c[0][0] == "stock_data.py" and "sector_rankings" in c[0][1]]
        assert len(sector_call) == 1
        assert sector_call[0][0] == ("stock_data.py", ["sector_rankings", "--top", "5", "--direction", "both"])


class TestMainFundamental:
    @patch("tools.gather.gather_fundamental")
    def test_fundamental_command(self, mock_gather, capsys):
        mock_gather.return_value = {"quote": {"price": 100}}
        import tools.gather

        with patch("sys.argv", ["gather.py", "fundamental", "600519"]):
            tools.gather.main()
        mock_gather.assert_called_once_with("600519")
