import json
from unittest.mock import patch

import pytest

from tools.watchlist import analyze_watchlist


class TestAnalyzeWatchlist:
    @patch("tools.watchlist.gather_analysis")
    def test_all_success(self, mock_gather):
        mock_gather.return_value = {"quote": {"price": 100}, "kline": []}
        result = analyze_watchlist(["600519", "000001"])
        assert result["summary"]["total"] == 2
        assert result["summary"]["success"] == 2
        assert result["summary"]["failed"] == 0
        assert "600519" in result["results"]
        assert "000001" in result["results"]
        assert result["results"]["600519"] == {"quote": {"price": 100}, "kline": []}

    @patch("tools.watchlist.gather_analysis")
    def test_partial_failure(self, mock_gather):
        def side_effect(symbol):
            if symbol == "INVALID":
                raise RuntimeError("fetch failed")
            return {"quote": {"price": 50}}

        mock_gather.side_effect = side_effect
        result = analyze_watchlist(["600519", "INVALID"])
        assert result["summary"]["total"] == 2
        assert result["summary"]["success"] == 1
        assert result["summary"]["failed"] == 1
        assert result["results"]["600519"] == {"quote": {"price": 50}}
        assert result["results"]["INVALID"] is None

    @patch("tools.watchlist.gather_analysis")
    def test_all_fail(self, mock_gather):
        mock_gather.side_effect = Exception("network error")
        result = analyze_watchlist(["A", "B", "C"])
        assert result["summary"]["total"] == 3
        assert result["summary"]["success"] == 0
        assert result["summary"]["failed"] == 3
        for v in result["results"].values():
            assert v is None

    @patch("tools.watchlist.gather_analysis")
    def test_single_stock(self, mock_gather):
        mock_gather.return_value = {"technical": {"score": 85}}
        result = analyze_watchlist(["300750"])
        assert result["summary"]["total"] == 1
        assert result["summary"]["success"] == 1
        assert result["results"]["300750"] == {"technical": {"score": 85}}

    @patch("tools.watchlist.gather_analysis")
    def test_workers_param(self, mock_gather):
        mock_gather.return_value = {"data": 1}
        result = analyze_watchlist(["600519", "000001", "300750"], workers=1)
        assert result["summary"]["success"] == 3

    @patch("tools.watchlist.gather_analysis")
    def test_empty_list(self, mock_gather):
        result = analyze_watchlist([])
        assert result["summary"]["total"] == 0
        assert result["summary"]["success"] == 0
        assert result["summary"]["failed"] == 0
        assert result["results"] == {}
        mock_gather.assert_not_called()


class TestMain:
    @patch("tools.watchlist.analyze_watchlist")
    def test_analyze_command(self, mock_analyze, capsys):
        mock_analyze.return_value = {
            "results": {"600519": {"quote": {}}},
            "summary": {"total": 1, "success": 1, "failed": 0},
        }
        import tools.watchlist

        with patch("sys.argv", ["watchlist.py", "analyze", "600519,000001"]):
            tools.watchlist.main()
        mock_analyze.assert_called_once_with(["600519", "000001"], workers=3)
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["summary"]["total"] == 1

    @patch("tools.watchlist.analyze_watchlist")
    def test_custom_workers(self, mock_analyze, capsys):
        mock_analyze.return_value = {"results": {}, "summary": {"total": 0, "success": 0, "failed": 0}}
        import tools.watchlist

        with patch("sys.argv", ["watchlist.py", "analyze", "600519", "--workers", "5"]):
            tools.watchlist.main()
        mock_analyze.assert_called_once_with(["600519"], workers=5)

    def test_no_command_exits(self):
        import tools.watchlist

        with patch("sys.argv", ["watchlist.py"]):
            with pytest.raises(SystemExit) as exc_info:
                tools.watchlist.main()
            assert exc_info.value.code == 1

    def test_empty_symbols_exits(self, capsys):
        import tools.watchlist

        with patch("sys.argv", ["watchlist.py", "analyze", ",,,"]):
            with pytest.raises(SystemExit) as exc_info:
                tools.watchlist.main()
            assert exc_info.value.code == 1
