"""Tests for failover logic in tools/stock_data.py."""

import json
import subprocess
import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from tools.stock_data import (
    _capital_flow_efinance,
    _constituents_em,
    _constituents_sina,
    _failover,
    _fuzzy_match_sector,
    _kline_akshare,
    _kline_alphavantage,
    _kline_finnhub,
    _kline_longbridge,
    _kline_pytdx,
    _kline_tushare,
    _kline_yfinance,
    _news_search_intel_fallback,
    _quote_alphavantage,
    _quote_finnhub,
    _quote_longbridge,
    _quote_pytdx,
    _quote_tushare,
    _quote_yfinance,
    _sector_cache_set,
    _sector_rankings_efinance,
    _stock_boards_efinance,
    _stock_boards_em,
    _stock_boards_from_cache,
    _stock_boards_xueqiu,
    _stock_sectors_hk,
    _to_baostock_code,
    _xq_symbol,
    _yf_hk_symbol,
    cmd_capital_flow,
    cmd_news,
    cmd_sector_constituents,
    cmd_sector_rankings,
    cmd_stock_info,
    financials_yf,
    kline_a,
    kline_yf,
    quote_a,
    quote_yf,
    resolve_stock_sectors,
    sector_constituents_a,
)


class TestFailover:
    def test_first_source_succeeds(self):
        result = _failover([("src1", lambda: {"data": 1}), ("src2", lambda: {"data": 2})])
        assert result == {"data": 1}

    def test_first_fails_second_succeeds(self):
        def fail():
            raise ValueError("source1 down")

        result = _failover([("src1", fail), ("src2", lambda: {"data": 2})])
        assert result == {"data": 2}

    def test_all_fail_raises_last_error(self):
        def fail1():
            raise ValueError("error1")

        def fail2():
            raise RuntimeError("error2")

        with pytest.raises(RuntimeError, match="error2"):
            _failover([("src1", fail1), ("src2", fail2)])

    def test_falsy_result_skipped(self):
        result = _failover([("src1", lambda: None), ("src2", lambda: [1, 2, 3])])
        assert result == [1, 2, 3]

    def test_empty_list_skipped(self):
        result = _failover([("src1", lambda: []), ("src2", lambda: [1])])
        assert result == [1]

    def test_all_return_none_no_exception(self):
        result = _failover([("src1", lambda: None), ("src2", lambda: None)])
        assert result is None

    def test_label_param_accepted(self):
        result = _failover([("src1", lambda: "ok")], label="test:label")
        assert result == "ok"


@pytest.fixture
def mock_yfinance():
    """Inject a mock yfinance module if not installed."""
    mock_yf = MagicMock()
    with patch.dict(sys.modules, {"yfinance": mock_yf}):
        yield mock_yf


@pytest.fixture
def mock_finnhub():
    """Inject a mock finnhub module if not installed."""
    mock_fh = MagicMock()
    with patch.dict(sys.modules, {"finnhub": mock_fh}):
        yield mock_fh


class TestKlineYfinance:
    def test_returns_data(self, mock_yfinance):
        import pandas as pd

        df = pd.DataFrame(
            {
                "Date": pd.date_range("2026-01-01", periods=3),
                "Open": [10.0, 11.0, 12.0],
                "High": [11.0, 12.0, 13.0],
                "Low": [9.0, 10.0, 11.0],
                "Close": [10.5, 11.5, 12.5],
                "Volume": [1000, 2000, 3000],
            }
        )
        mock_yfinance.download.return_value = df
        result = _kline_yfinance("AAPL", "daily", 3)
        assert len(result) == 3
        assert "close" in result[0]
        assert result[0]["close"] == 10.5

    def test_empty_raises(self, mock_yfinance):
        import pandas as pd

        mock_yfinance.download.return_value = pd.DataFrame()
        with pytest.raises(ValueError, match="empty data"):
            _kline_yfinance("AAPL", "daily", 10)


class TestKlineFinnhub:
    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
    def test_returns_data(self, mock_finnhub):
        mock_client = MagicMock()
        mock_finnhub.Client.return_value = mock_client
        mock_client.stock_candles.return_value = {
            "s": "ok",
            "c": [100.0, 101.0],
            "o": [99.0, 100.0],
            "h": [102.0, 103.0],
            "l": [98.0, 99.0],
            "v": [5000, 6000],
            "t": [1716000000, 1716086400],
        }
        result = _kline_finnhub("AAPL", "daily", 5)
        assert len(result) == 2
        assert result[0]["close"] == 100.0
        assert result[0]["open"] == 99.0

    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_raises(self):
        import os

        os.environ.pop("FINNHUB_API_KEY", None)
        with pytest.raises(ValueError, match="FINNHUB_API_KEY not set"):
            _kline_finnhub("AAPL", "daily", 10)

    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
    def test_no_data_raises(self, mock_finnhub):
        mock_client = MagicMock()
        mock_finnhub.Client.return_value = mock_client
        mock_client.stock_candles.return_value = {"s": "no_data", "c": []}
        with pytest.raises(ValueError, match="no data"):
            _kline_finnhub("AAPL", "daily", 10)

    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
    def test_hk_suffix_removed(self, mock_finnhub):
        mock_client = MagicMock()
        mock_finnhub.Client.return_value = mock_client
        mock_client.stock_candles.return_value = {
            "s": "ok",
            "c": [50.0],
            "o": [49.0],
            "h": [51.0],
            "l": [48.0],
            "v": [1000],
            "t": [1716000000],
        }
        _kline_finnhub("0700.HK", "daily", 5)
        call_args = mock_client.stock_candles.call_args
        assert call_args[0][0] == "0700"


class TestQuoteYfinance:
    def test_returns_data(self, mock_yfinance):
        mock_ticker = MagicMock()
        mock_yfinance.Ticker.return_value = mock_ticker
        mock_ticker.info = {
            "regularMarketPrice": 150.0,
            "shortName": "Apple Inc.",
            "regularMarketChange": 2.5,
            "regularMarketChangePercent": 1.7,
            "regularMarketVolume": 50000000,
            "regularMarketDayHigh": 152.0,
            "regularMarketDayLow": 148.0,
            "regularMarketOpen": 149.0,
            "regularMarketPreviousClose": 147.5,
            "marketCap": 2400000000000,
            "trailingPE": 28.5,
            "priceToBook": 45.0,
        }
        result = _quote_yfinance("AAPL")
        assert result["price"] == 150.0
        assert result["name"] == "Apple Inc."

    def test_no_data_raises(self, mock_yfinance):
        mock_ticker = MagicMock()
        mock_yfinance.Ticker.return_value = mock_ticker
        mock_ticker.info = {}
        with pytest.raises(ValueError, match="No data"):
            _quote_yfinance("INVALID")


class TestQuoteFinnhub:
    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
    def test_returns_data(self, mock_finnhub):
        mock_client = MagicMock()
        mock_finnhub.Client.return_value = mock_client
        mock_client.quote.return_value = {
            "c": 150.0,
            "d": 2.5,
            "dp": 1.7,
            "h": 152.0,
            "l": 148.0,
            "o": 149.0,
            "pc": 147.5,
        }
        result = _quote_finnhub("AAPL")
        assert result["price"] == 150.0
        assert result["change"] == 2.5

    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_raises(self):
        import os

        os.environ.pop("FINNHUB_API_KEY", None)
        with pytest.raises(ValueError, match="FINNHUB_API_KEY not set"):
            _quote_finnhub("AAPL")

    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
    def test_zero_price_raises(self, mock_finnhub):
        mock_client = MagicMock()
        mock_finnhub.Client.return_value = mock_client
        mock_client.quote.return_value = {"c": 0, "d": 0, "dp": 0, "h": 0, "l": 0, "o": 0, "pc": 0}
        with pytest.raises(ValueError, match="no quote"):
            _quote_finnhub("INVALID")

    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
    def test_hk_suffix_removed(self, mock_finnhub):
        mock_client = MagicMock()
        mock_finnhub.Client.return_value = mock_client
        mock_client.quote.return_value = {
            "c": 350.0,
            "d": 5.0,
            "dp": 1.4,
            "h": 355.0,
            "l": 345.0,
            "o": 348.0,
            "pc": 345.0,
        }
        _quote_finnhub("0700.HK")
        mock_client.quote.assert_called_with("0700")


class TestKlineYfFailover:
    @patch("tools.stock_data._kline_finnhub")
    @patch("tools.stock_data._kline_yfinance")
    def test_yfinance_success_no_finnhub(self, mock_yf, mock_fh):
        mock_yf.return_value = [{"close": 100}]
        result = kline_yf("AAPL", "daily", 10)
        assert result == [{"close": 100}]
        mock_fh.assert_not_called()

    @patch("tools.stock_data._kline_finnhub")
    @patch("tools.stock_data._kline_yfinance")
    def test_yfinance_fails_finnhub_used(self, mock_yf, mock_fh):
        mock_yf.side_effect = ValueError("yfinance down")
        mock_fh.return_value = [{"close": 200}]
        result = kline_yf("AAPL", "daily", 10)
        assert result == [{"close": 200}]

    @patch("tools.stock_data._kline_alphavantage")
    @patch("tools.stock_data._kline_longbridge")
    @patch("tools.stock_data._kline_finnhub")
    @patch("tools.stock_data._kline_yfinance")
    def test_both_fail_raises(self, mock_yf, mock_fh, mock_lb, mock_av):
        mock_yf.side_effect = ValueError("yf down")
        mock_fh.side_effect = ValueError("fh down")
        mock_lb.side_effect = ValueError("lb down")
        mock_av.side_effect = ValueError("av down")
        with pytest.raises(ValueError, match="av down"):
            kline_yf("AAPL", "daily", 10)


class TestQuoteYfFailover:
    @patch("tools.stock_data._quote_finnhub")
    @patch("tools.stock_data._quote_yfinance")
    def test_yfinance_success_no_finnhub(self, mock_yf, mock_fh):
        mock_yf.return_value = {"price": 150}
        result = quote_yf("AAPL")
        assert result == {"price": 150}
        mock_fh.assert_not_called()

    @patch("tools.stock_data._quote_finnhub")
    @patch("tools.stock_data._quote_yfinance")
    def test_yfinance_fails_finnhub_used(self, mock_yf, mock_fh):
        mock_yf.side_effect = ValueError("yfinance down")
        mock_fh.return_value = {"price": 150}
        result = quote_yf("AAPL")
        assert result == {"price": 150}

    @patch("tools.stock_data._quote_alphavantage")
    @patch("tools.stock_data._quote_longbridge")
    @patch("tools.stock_data._quote_finnhub")
    @patch("tools.stock_data._quote_yfinance")
    def test_both_fail_raises(self, mock_yf, mock_fh, mock_lb, mock_av):
        mock_yf.side_effect = ValueError("yf down")
        mock_fh.side_effect = RuntimeError("fh down")
        mock_lb.side_effect = ValueError("lb down")
        mock_av.side_effect = ValueError("av down")
        with pytest.raises(ValueError, match="av down"):
            quote_yf("AAPL")


class TestCapitalFlowEfinance:
    @patch("efinance.stock.get_today_bill")
    def test_returns_data(self, mock_bill):
        import pandas as pd

        df = pd.DataFrame(
            {
                "日期": ["2026-05-19"],
                "主力净流入": [1000000],
                "超大单净流入": [500000],
                "大单净流入": [500000],
                "中单净流入": [-200000],
                "小单净流入": [-300000],
            }
        )
        mock_bill.return_value = df
        result = _capital_flow_efinance("600519")
        assert len(result) == 1
        assert result[0]["main_net_inflow"] == 1000000

    @patch("efinance.stock.get_today_bill")
    def test_empty_raises(self, mock_bill):
        import pandas as pd

        mock_bill.return_value = pd.DataFrame()
        with pytest.raises(ValueError, match="empty"):
            _capital_flow_efinance("600519")


class TestCmdCapitalFlowFailover:
    @patch("tools.stock_data._capital_flow_efinance")
    @patch("tools.stock_data._akshare_retry")
    def test_akshare_fails_efinance_used(self, mock_ak, mock_ef):
        mock_ak.side_effect = Exception("akshare down")
        mock_ef.return_value = [{"main_net_inflow": 500}]
        args = Namespace(symbol="600519", mode="detail")
        result = cmd_capital_flow(args)
        assert result == [{"main_net_inflow": 500}]

    @patch("tools.stock_data._capital_flow_efinance")
    @patch("tools.stock_data._akshare_retry")
    def test_both_fail_returns_error(self, mock_ak, mock_ef):
        mock_ak.side_effect = Exception("akshare down")
        mock_ef.side_effect = ValueError("efinance also down")
        args = Namespace(symbol="600519", mode="detail")
        result = cmd_capital_flow(args)
        assert "error" in result


class TestSectorRankingsEfinance:
    @patch("efinance.stock.get_belong_board")
    @patch("efinance.stock.get_realtime_quotes")
    def test_top_direction(self, mock_quotes, mock_boards):
        import pandas as pd

        mock_boards.return_value = ["板块1", "板块2"]
        df = pd.DataFrame(
            {
                "股票名称": ["半导体", "新能源", "医药", "银行"],
                "股票代码": ["BK001", "BK002", "BK003", "BK004"],
                "涨跌幅": ["3.5", "2.1", "-1.0", "-2.5"],
                "成交量": ["100", "200", "150", "80"],
                "成交额": ["1000", "2000", "1500", "800"],
            }
        )
        mock_quotes.return_value = df
        result = _sector_rankings_efinance(2, "top")
        assert len(result) == 2
        assert result[0]["change_pct"] == 3.5

    @patch("efinance.stock.get_belong_board")
    @patch("efinance.stock.get_realtime_quotes")
    def test_both_direction(self, mock_quotes, mock_boards):
        import pandas as pd

        mock_boards.return_value = ["板块1"]
        df = pd.DataFrame(
            {
                "股票名称": ["A", "B", "C", "D"],
                "股票代码": ["1", "2", "3", "4"],
                "涨跌幅": ["5.0", "3.0", "-2.0", "-4.0"],
                "成交量": ["100", "200", "150", "80"],
                "成交额": ["1000", "2000", "1500", "800"],
            }
        )
        mock_quotes.return_value = df
        result = _sector_rankings_efinance(2, "both")
        assert "top" in result
        assert "bottom" in result
        assert len(result["top"]) == 2
        assert len(result["bottom"]) == 2

    @patch("efinance.stock.get_belong_board")
    @patch("efinance.stock.get_realtime_quotes")
    def test_empty_raises(self, mock_quotes, mock_boards):
        import pandas as pd

        mock_boards.return_value = []
        mock_quotes.return_value = pd.DataFrame()
        with pytest.raises(ValueError, match="unavailable"):
            _sector_rankings_efinance(5, "top")


class TestCmdSectorRankingsFailover:
    @patch("tools.stock_data._sector_rankings_efinance")
    @patch("tools.stock_data._akshare_retry")
    def test_akshare_fails_efinance_used(self, mock_ak, mock_ef):
        mock_ak.side_effect = Exception("akshare down")
        mock_ef.return_value = [{"name": "半导体", "change_pct": 3.5}]
        args = Namespace(top=5, direction="top")
        result = cmd_sector_rankings(args)
        assert result == [{"name": "半导体", "change_pct": 3.5}]

    @patch("tools.stock_data._sector_rankings_efinance")
    @patch("tools.stock_data._akshare_retry")
    def test_both_fail_returns_error(self, mock_ak, mock_ef):
        mock_ak.side_effect = Exception("akshare down")
        mock_ef.side_effect = ValueError("efinance also down")
        args = Namespace(top=5, direction="top")
        result = cmd_sector_rankings(args)
        assert "error" in result


class TestNewsSearchIntelFallback:
    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"title": "News 1", "url": "http://example.com"}]),
        )
        result = _news_search_intel_fallback("600519")
        assert len(result) == 1
        assert result[0]["title"] == "News 1"
        assert result[0]["source"] == "search"

    @patch("subprocess.run")
    def test_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = _news_search_intel_fallback("600519")
        assert result == []

    @patch("subprocess.run")
    def test_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        result = _news_search_intel_fallback("600519")
        assert result == []

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=30)
        result = _news_search_intel_fallback("600519")
        assert result == []

    @patch("subprocess.run")
    def test_limits_to_10_items(self, mock_run):
        items = [{"title": f"News {i}", "url": f"http://example.com/{i}"} for i in range(15)]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(items))
        result = _news_search_intel_fallback("600519")
        assert len(result) == 10


class TestCmdNewsFailover:
    @patch("tools.stock_data._news_search_intel_fallback")
    @patch("tools.stock_data._akshare_retry")
    def test_akshare_fails_fallback_used(self, mock_ak, mock_fallback):
        mock_ak.side_effect = Exception("akshare down")
        mock_fallback.return_value = [{"title": "Fallback news", "url": "http://x.com", "source": "search"}]
        args = Namespace(symbol="600519", days=3)
        result = cmd_news(args)
        assert result[0]["title"] == "Fallback news"

    @patch("tools.stock_data._news_search_intel_fallback")
    @patch("tools.stock_data._akshare_retry")
    def test_akshare_empty_triggers_fallback(self, mock_ak, mock_fallback):
        import pandas as pd

        mock_ak.return_value = pd.DataFrame()
        mock_fallback.return_value = [{"title": "Found via search", "url": "http://x.com", "source": "search"}]
        args = Namespace(symbol="600519", days=3)
        result = cmd_news(args)
        assert result[0]["title"] == "Found via search"


# --------------- New data source tests ---------------


class TestKlineTushare:
    @patch.dict("os.environ", {"TUSHARE_TOKEN": "test_token"})
    @patch("requests.post")
    def test_returns_data(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {
                "code": 0,
                "data": {
                    "fields": ["trade_date", "open", "high", "low", "close", "vol", "amount", "pct_chg"],
                    "items": [
                        ["20260519", 10.0, 11.0, 9.0, 10.5, 1000, 10500, 1.5],
                        ["20260518", 9.5, 10.5, 9.0, 10.0, 900, 9000, 0.5],
                    ],
                },
            }
        )
        result = _kline_tushare("600519", "daily", 5)
        assert len(result) == 2
        assert result[0]["close"] == 10.0

    @patch.dict("os.environ", {}, clear=True)
    def test_no_token_raises(self):
        import os

        os.environ.pop("TUSHARE_TOKEN", None)
        with pytest.raises(ValueError, match="TUSHARE_TOKEN not set"):
            _kline_tushare("600519", "daily", 10)

    @patch.dict("os.environ", {"TUSHARE_TOKEN": "test_token"})
    @patch("requests.post")
    def test_error_response_raises(self, mock_post):
        mock_post.return_value = MagicMock(json=lambda: {"code": -1, "msg": "rate limited"})
        with pytest.raises(ValueError, match="tushare returned no data"):
            _kline_tushare("600519", "daily", 10)


class TestQuoteTushare:
    @patch.dict("os.environ", {"TUSHARE_TOKEN": "test_token"})
    @patch("requests.post")
    def test_returns_data(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {
                "code": 0,
                "data": {
                    "fields": [
                        "name",
                        "price",
                        "change",
                        "pct_chg",
                        "vol",
                        "amount",
                        "high",
                        "low",
                        "open",
                        "pre_close",
                    ],
                    "items": [["贵州茅台", 1800.0, 20.0, 1.12, 5000, 9000000, 1810.0, 1780.0, 1790.0, 1780.0]],
                },
            }
        )
        result = _quote_tushare("600519")
        assert result["price"] == 1800.0
        assert result["name"] == "贵州茅台"

    @patch.dict("os.environ", {}, clear=True)
    def test_no_token_raises(self):
        import os

        os.environ.pop("TUSHARE_TOKEN", None)
        with pytest.raises(ValueError, match="TUSHARE_TOKEN not set"):
            _quote_tushare("600519")


class TestKlinePytdx:
    """pytdx connect() returns bool, not a context manager — mocks must reflect that."""

    def test_returns_data(self):
        mock_api_cls = MagicMock()
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.connect.return_value = True
        mock_api.get_security_bars.return_value = [
            {
                "datetime": "2026-05-18 15:00",
                "open": 9.5,
                "high": 10.5,
                "low": 9.0,
                "close": 10.0,
                "vol": 900,
                "amount": 9000,
            },
            {
                "datetime": "2026-05-19 15:00",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "vol": 1000,
                "amount": 10500,
            },
        ]
        with patch.dict(sys.modules, {"pytdx": MagicMock(), "pytdx.hq": MagicMock(TdxHq_API=mock_api_cls)}):
            result = _kline_pytdx("600519", "daily", 5)
        assert len(result) == 2
        assert result[0]["close"] == 10.0
        mock_api.disconnect.assert_called_once()

    def test_empty_raises(self):
        mock_api_cls = MagicMock()
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.connect.return_value = True
        mock_api.get_security_bars.return_value = []
        with (
            patch.dict(sys.modules, {"pytdx": MagicMock(), "pytdx.hq": MagicMock(TdxHq_API=mock_api_cls)}),
            pytest.raises(ValueError, match="pytdx returned empty"),
        ):
            _kline_pytdx("600519", "daily", 10)


class TestQuotePytdx:
    def test_returns_data(self):
        mock_api_cls = MagicMock()
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.connect.return_value = True
        mock_api.get_security_quotes.return_value = [
            {
                "name": "贵州茅台",
                "price": 1800.0,
                "last_close": 1780.0,
                "vol": 5000,
                "high": 1810.0,
                "low": 1780.0,
                "open": 1790.0,
            }
        ]
        with patch.dict(sys.modules, {"pytdx": MagicMock(), "pytdx.hq": MagicMock(TdxHq_API=mock_api_cls)}):
            result = _quote_pytdx("600519")
        assert result["price"] == 1800.0
        assert result["change_pct"] == pytest.approx(1.1236, rel=0.01)
        mock_api.disconnect.assert_called_once()

    def test_connect_false_raises_connection_error_not_typeerror(self):
        """connect() returning False (server unreachable) must surface a clear ConnectionError."""
        mock_api_cls = MagicMock()
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.connect.return_value = False
        with (
            patch.dict(sys.modules, {"pytdx": MagicMock(), "pytdx.hq": MagicMock(TdxHq_API=mock_api_cls)}),
            pytest.raises(ConnectionError, match="pytdx.*connect"),
        ):
            _quote_pytdx("600519")

    def test_connect_exception_falls_over_to_next_server(self):
        """A raising connect() on one server must not abort — the next server is tried."""
        mock_api_cls = MagicMock()
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.connect.side_effect = [OSError("timed out"), True]
        mock_api.get_security_quotes.return_value = [
            {"name": "x", "price": 1.0, "last_close": 1.0, "vol": 1, "high": 1.0, "low": 1.0, "open": 1.0}
        ]
        with patch.dict(sys.modules, {"pytdx": MagicMock(), "pytdx.hq": MagicMock(TdxHq_API=mock_api_cls)}):
            result = _quote_pytdx("600519")
        assert result["price"] == 1.0
        assert mock_api.connect.call_count == 2


class TestToBaostockCode:
    """Index codes collide with SZ stocks on 000xxx — they must map to the index exchange."""

    def test_shanghai_index_codes(self):
        assert _to_baostock_code("000001") == "sh.000001"  # 上证综指, not 平安银行
        assert _to_baostock_code("000300") == "sh.000300"  # 沪深300
        assert _to_baostock_code("000016") == "sh.000016"  # 上证50
        assert _to_baostock_code("000688") == "sh.000688"  # 科创50

    def test_shenzhen_index_codes(self):
        assert _to_baostock_code("399001") == "sz.399001"  # 深证成指
        assert _to_baostock_code("399006") == "sz.399006"  # 创业板指

    def test_sz_stocks_not_hijacked_by_whitelist(self):
        assert _to_baostock_code("000063") == "sz.000063"  # 中兴通讯 stays SZ
        assert _to_baostock_code("000333") == "sz.000333"  # 美的集团 stays SZ

    def test_stock_prefixes_unchanged(self):
        assert _to_baostock_code("600519") == "sh.600519"
        assert _to_baostock_code("510300") == "sh.510300"
        assert _to_baostock_code("300750") == "sz.300750"


class TestKlineLongbridge:
    @patch.dict(
        "os.environ",
        {"LONGBRIDGE_APP_KEY": "k", "LONGBRIDGE_APP_SECRET": "s", "LONGBRIDGE_ACCESS_TOKEN": "t"},
    )
    def test_no_data_raises(self):
        mock_ctx = MagicMock()
        mock_ctx.candlesticks.return_value = []
        with (
            patch.dict(
                sys.modules,
                {"longport": MagicMock(), "longport.openapi": MagicMock(QuoteContext=MagicMock(return_value=mock_ctx))},
            ),
            pytest.raises(ValueError, match="longbridge returned no kline"),
        ):
            _kline_longbridge("AAPL", "daily", 10)

    @patch.dict("os.environ", {}, clear=True)
    def test_no_credentials_raises(self):
        import os

        os.environ.pop("LONGBRIDGE_APP_KEY", None)
        os.environ.pop("LONGBRIDGE_APP_SECRET", None)
        os.environ.pop("LONGBRIDGE_ACCESS_TOKEN", None)
        with pytest.raises(ValueError, match="LONGBRIDGE credentials not set"):
            _kline_longbridge("AAPL", "daily", 10)


class TestQuoteLongbridge:
    @patch.dict("os.environ", {}, clear=True)
    def test_no_credentials_raises(self):
        import os

        os.environ.pop("LONGBRIDGE_APP_KEY", None)
        os.environ.pop("LONGBRIDGE_APP_SECRET", None)
        os.environ.pop("LONGBRIDGE_ACCESS_TOKEN", None)
        with pytest.raises(ValueError, match="LONGBRIDGE credentials not set"):
            _quote_longbridge("AAPL")


class TestKlineAlphavantage:
    @patch.dict("os.environ", {"ALPHAVANTAGE_API_KEY": "test_key"})
    @patch("requests.get")
    def test_returns_data(self, mock_get):
        mock_get.return_value = MagicMock(
            json=lambda: {
                "Time Series (Daily)": {
                    "2026-05-19": {
                        "1. open": "150.0",
                        "2. high": "152.0",
                        "3. low": "148.0",
                        "4. close": "151.0",
                        "6. volume": "50000",
                    },
                    "2026-05-18": {
                        "1. open": "149.0",
                        "2. high": "151.0",
                        "3. low": "147.0",
                        "4. close": "150.0",
                        "6. volume": "45000",
                    },
                }
            }
        )
        result = _kline_alphavantage("AAPL", "daily", 5)
        assert len(result) == 2
        assert result[0]["close"] == 150.0

    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_raises(self):
        import os

        os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        with pytest.raises(ValueError, match="ALPHAVANTAGE_API_KEY not set"):
            _kline_alphavantage("AAPL", "daily", 10)

    @patch.dict("os.environ", {"ALPHAVANTAGE_API_KEY": "test_key"})
    @patch("requests.get")
    def test_rate_limit_raises(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {"Note": "API rate limit reached"})
        with pytest.raises(ValueError, match="alphavantage returned no data"):
            _kline_alphavantage("AAPL", "daily", 10)


class TestQuoteAlphavantage:
    @patch.dict("os.environ", {"ALPHAVANTAGE_API_KEY": "test_key"})
    @patch("requests.get")
    def test_returns_data(self, mock_get):
        mock_get.return_value = MagicMock(
            json=lambda: {
                "Global Quote": {
                    "05. price": "150.25",
                    "09. change": "2.50",
                    "10. change percent": "1.69%",
                    "06. volume": "50000000",
                    "03. high": "152.00",
                    "04. low": "148.00",
                    "02. open": "149.00",
                    "08. previous close": "147.75",
                }
            }
        )
        result = _quote_alphavantage("AAPL")
        assert result["price"] == 150.25
        assert result["change"] == 2.5

    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_raises(self):
        import os

        os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        with pytest.raises(ValueError, match="ALPHAVANTAGE_API_KEY not set"):
            _quote_alphavantage("AAPL")


class TestKlineAFailover:
    @patch("tools.stock_data._kline_baostock")
    @patch("tools.stock_data._kline_pytdx")
    @patch("tools.stock_data._kline_efinance")
    @patch("tools.stock_data._kline_tushare")
    @patch("tools.stock_data._kline_akshare")
    def test_akshare_success_others_not_called(self, mock_ak, mock_ts, mock_ef, mock_ptdx, mock_bs):
        mock_ak.return_value = [{"close": 100}]
        result = kline_a("600519", "daily", 10)
        assert result == [{"close": 100}]
        mock_ts.assert_not_called()
        mock_ef.assert_not_called()

    @patch("tools.stock_data._kline_baostock")
    @patch("tools.stock_data._kline_pytdx")
    @patch("tools.stock_data._kline_efinance")
    @patch("tools.stock_data._kline_tushare")
    @patch("tools.stock_data._kline_akshare")
    def test_falls_through_to_pytdx(self, mock_ak, mock_ts, mock_ef, mock_ptdx, mock_bs):
        mock_ak.side_effect = ValueError("down")
        mock_ts.side_effect = ValueError("down")
        mock_ef.side_effect = ValueError("down")
        mock_ptdx.return_value = [{"close": 200}]
        result = kline_a("600519", "daily", 10)
        assert result == [{"close": 200}]
        mock_bs.assert_not_called()

    @patch("tools.stock_data._kline_baostock")
    @patch("tools.stock_data._kline_pytdx")
    @patch("tools.stock_data._kline_efinance")
    @patch("tools.stock_data._kline_tushare")
    @patch("tools.stock_data._kline_akshare")
    def test_all_fail_raises_last(self, mock_ak, mock_ts, mock_ef, mock_ptdx, mock_bs):
        mock_ak.side_effect = ValueError("ak down")
        mock_ts.side_effect = ValueError("ts down")
        mock_ef.side_effect = ValueError("ef down")
        mock_ptdx.side_effect = ValueError("ptdx down")
        mock_bs.side_effect = ValueError("bs down")
        with pytest.raises(ValueError, match="bs down"):
            kline_a("600519", "daily", 10)


class TestQuoteAFailover:
    @patch("tools.stock_data._quote_pytdx")
    @patch("tools.stock_data._quote_efinance")
    @patch("tools.stock_data._quote_tushare")
    @patch("tools.stock_data._quote_akshare")
    def test_akshare_success(self, mock_ak, mock_ts, mock_ef, mock_ptdx):
        mock_ak.return_value = {"price": 1800}
        result = quote_a("600519")
        assert result == {"price": 1800}
        mock_ts.assert_not_called()

    @patch("tools.stock_data._quote_pytdx")
    @patch("tools.stock_data._quote_efinance")
    @patch("tools.stock_data._quote_tushare")
    @patch("tools.stock_data._quote_akshare")
    def test_falls_through_to_efinance(self, mock_ak, mock_ts, mock_ef, mock_ptdx):
        mock_ak.side_effect = ValueError("down")
        mock_ts.side_effect = ValueError("down")
        mock_ef.return_value = {"price": 1800}
        result = quote_a("600519")
        assert result == {"price": 1800}
        mock_ptdx.assert_not_called()

    @patch("tools.stock_data._quote_pytdx")
    @patch("tools.stock_data._quote_efinance")
    @patch("tools.stock_data._quote_tushare")
    @patch("tools.stock_data._quote_akshare")
    def test_all_fail_raises_last(self, mock_ak, mock_ts, mock_ef, mock_ptdx):
        mock_ak.side_effect = ValueError("ak down")
        mock_ts.side_effect = ValueError("ts down")
        mock_ef.side_effect = ValueError("ef down")
        mock_ptdx.side_effect = ValueError("ptdx down")
        with pytest.raises(ValueError, match="ptdx down"):
            quote_a("600519")


class TestKlineYfFailoverExtended:
    @patch("tools.stock_data._kline_alphavantage")
    @patch("tools.stock_data._kline_longbridge")
    @patch("tools.stock_data._kline_finnhub")
    @patch("tools.stock_data._kline_yfinance")
    def test_falls_through_to_longbridge(self, mock_yf, mock_fh, mock_lb, mock_av):
        mock_yf.side_effect = ValueError("down")
        mock_fh.side_effect = ValueError("down")
        mock_lb.return_value = [{"close": 300}]
        result = kline_yf("AAPL", "daily", 10)
        assert result == [{"close": 300}]
        mock_av.assert_not_called()

    @patch("tools.stock_data._kline_alphavantage")
    @patch("tools.stock_data._kline_longbridge")
    @patch("tools.stock_data._kline_finnhub")
    @patch("tools.stock_data._kline_yfinance")
    def test_us_stock_includes_alphavantage(self, mock_yf, mock_fh, mock_lb, mock_av):
        mock_yf.side_effect = ValueError("down")
        mock_fh.side_effect = ValueError("down")
        mock_lb.side_effect = ValueError("down")
        mock_av.return_value = [{"close": 400}]
        result = kline_yf("AAPL", "daily", 10)
        assert result == [{"close": 400}]

    @patch("tools.stock_data._kline_alphavantage")
    @patch("tools.stock_data._kline_longbridge")
    @patch("tools.stock_data._kline_finnhub")
    @patch("tools.stock_data._kline_yfinance")
    def test_hk_stock_no_alphavantage(self, mock_yf, mock_fh, mock_lb, mock_av):
        mock_yf.side_effect = ValueError("down")
        mock_fh.side_effect = ValueError("down")
        mock_lb.side_effect = ValueError("down")
        with pytest.raises(ValueError, match="down"):
            kline_yf("0700.HK", "daily", 10)
        mock_av.assert_not_called()


class TestNewMarketKlineChain:
    """JP/KR/TW kline/quote should use yfinance only — finnhub/longbridge/alphavantage don't cover them."""

    @patch("tools.stock_data._kline_finnhub")
    @patch("tools.stock_data._kline_yfinance")
    def test_jp_kline_yfinance_only(self, mock_yf, mock_fh):
        mock_yf.return_value = [{"close": 100}]
        result = kline_yf("7203.T", "daily", 10)
        assert result == [{"close": 100}]
        mock_fh.assert_not_called()

    @patch("tools.stock_data._kline_finnhub")
    @patch("tools.stock_data._kline_yfinance")
    def test_kr_kline_no_finnhub_fallback(self, mock_yf, mock_fh):
        mock_yf.side_effect = ValueError("yf down")
        mock_fh.return_value = [{"close": 200}]
        with pytest.raises(ValueError, match="yf down"):
            kline_yf("005930.KS", "daily", 10)
        mock_fh.assert_not_called()

    @patch("tools.stock_data._quote_finnhub")
    @patch("tools.stock_data._quote_yfinance")
    def test_tw_quote_no_finnhub_fallback(self, mock_yf, mock_fh):
        mock_yf.side_effect = ValueError("yf down")
        mock_fh.return_value = {"price": 1000}
        with pytest.raises(ValueError, match="yf down"):
            quote_yf("2330.TW")
        mock_fh.assert_not_called()

    @patch("tools.stock_data._quote_finnhub")
    @patch("tools.stock_data._quote_yfinance")
    def test_hk_quote_still_has_finnhub_fallback(self, mock_yf, mock_fh):
        mock_yf.side_effect = ValueError("yf down")
        mock_fh.return_value = {"price": 500}
        assert quote_yf("0700.HK") == {"price": 500}


class TestAkshareETF:
    """A-share ETFs (51/15xxxx etc.) must route to fund APIs, not stock APIs."""

    def _etf_hist_df(self):
        import pandas as pd

        return pd.DataFrame(
            {
                "日期": ["2026-01-05", "2026-01-06"],
                "开盘": [4.0, 4.1],
                "收盘": [4.05, 4.15],
                "最高": [4.1, 4.2],
                "最低": [3.9, 4.0],
                "成交量": [100000, 200000],
                "成交额": [400000.0, 800000.0],
                "涨跌幅": [1.25, 2.47],
                "换手率": [3.0, 5.0],
            }
        )

    def test_etf_kline_uses_fund_api(self):
        mock_ak = MagicMock()
        mock_ak.fund_etf_hist_em.return_value = self._etf_hist_df()
        mock_ak.stock_zh_a_hist.side_effect = AssertionError("stock api must not be called for ETF")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _kline_akshare("510300", "daily", 2)
        assert len(result) == 2
        assert result[0]["close"] == 4.05
        mock_ak.fund_etf_hist_em.assert_called_once()

    def test_stock_kline_still_uses_stock_api(self):
        mock_ak = MagicMock()
        mock_ak.stock_zh_a_hist.return_value = self._etf_hist_df()
        mock_ak.fund_etf_hist_em.side_effect = AssertionError("fund api must not be called for stock")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _kline_akshare("600519", "daily", 2)
        assert len(result) == 2
        mock_ak.stock_zh_a_hist.assert_called_once()

    def test_etf_quote_uses_fund_spot(self):
        import pandas as pd

        mock_ak = MagicMock()
        mock_ak.fund_etf_spot_em.return_value = pd.DataFrame(
            {
                "代码": ["510300"],
                "名称": ["沪深300ETF"],
                "最新价": [4.05],
                "涨跌幅": [1.25],
                "成交量": [100000],
                "成交额": [400000.0],
                "开盘价": [4.0],
                "最高价": [4.1],
                "最低价": [3.9],
                "昨收": [4.0],
                "换手率": [3.0],
            }
        )
        mock_ak.stock_zh_a_spot_em.side_effect = AssertionError("stock spot must not be called first for ETF")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = quote_a("510300")
        assert result["name"] == "沪深300ETF"
        assert result["price"] == 4.05
        assert result["is_etf"] is True

    @patch("tools.stock_data._quote_pytdx")
    @patch("tools.stock_data._quote_efinance")
    @patch("tools.stock_data._quote_tushare")
    @patch("tools.stock_data._quote_akshare")
    def test_stock_quote_has_no_etf_source(self, mock_ak, mock_ts, mock_ef, mock_ptdx):
        """Non-ETF A-share quote chain unchanged: no fund spot attempt."""
        mock_ak.return_value = {"price": 1800}
        result = quote_a("600519")
        assert result == {"price": 1800}
        mock_ak.assert_called_once()


# --------------- sector constituents / stock sectors (issue #18) ---------------


class TestFuzzyMatchSector:
    def test_exact_match_wins(self):
        assert _fuzzy_match_sector("创新药", ["创新药", "创新药ETF"]) == "创新药"

    def test_unique_contains_match(self):
        assert _fuzzy_match_sector("创新", ["创新药", "半导体"]) == "创新药"

    def test_ambiguous_returns_none(self):
        assert _fuzzy_match_sector("创新", ["创新药", "创新材料"]) is None

    def test_no_match_returns_none(self):
        assert _fuzzy_match_sector("不存在的板块", ["半导体"]) is None

    def test_board_suffix_stripped_to_exact(self):
        # 情报关键词「创新药板块」必须命中板块「创新药」
        assert _fuzzy_match_sector("创新药板块", ["创新药", "半导体"]) == "创新药"

    def test_industry_suffix_stripped_to_exact(self):
        assert _fuzzy_match_sector("酿酒行业", ["酿酒", "半导体"]) == "酿酒"

    def test_whitespace_stripped(self):
        assert _fuzzy_match_sector(" 创新药 ", ["创新药", "半导体"]) == "创新药"

    def test_etf_like_query_not_absorbed(self):
        # candidate∈query was removed: 「创新药ETF」 must not be absorbed into the 创新药 board
        assert _fuzzy_match_sector("创新药ETF", ["创新药", "半导体"]) is None

    def test_exact_board_name_beats_suffix_stripping(self):
        # a board literally named 白酒概念 must not be hijacked by the normalized 白酒
        assert _fuzzy_match_sector("白酒概念", ["白酒概念", "白酒"]) == "白酒概念"

    def test_suffix_only_query_returns_none(self):
        assert _fuzzy_match_sector("板块", ["创新药"]) is None


class TestConstituentsEm:
    """Eastmoney board constituents via akshare, with fuzzy name resolution."""

    def _mock_ak(self, industry_names, concept_names, cons_df):
        import pandas as pd

        mock_ak = MagicMock()
        mock_ak.stock_board_industry_name_em.return_value = pd.DataFrame({"板块名称": industry_names})
        mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame({"板块名称": concept_names})
        mock_ak.stock_board_industry_cons_em.return_value = cons_df
        mock_ak.stock_board_concept_cons_em.return_value = cons_df
        return mock_ak

    def test_industry_exact_match(self):
        import pandas as pd

        cons_df = pd.DataFrame({"代码": ["600276", "688235"], "名称": ["恒瑞医药", "百济神州"]})
        mock_ak = self._mock_ak(["医药生物", "半导体"], ["创新药"], cons_df)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _constituents_em("医药生物", "industry")
        assert result["sector"] == "医药生物"
        assert result["board_type"] == "industry"
        assert result["source"] == "eastmoney"
        assert result["count"] == 2
        assert result["constituents"][0] == {"code": "600276", "name": "恒瑞医药"}
        mock_ak.stock_board_industry_cons_em.assert_called_once_with(symbol="医药生物")

    def test_fuzzy_resolves_concept_board(self):
        import pandas as pd

        cons_df = pd.DataFrame({"代码": ["600276"], "名称": ["恒瑞医药"]})
        mock_ak = self._mock_ak(["半导体", "白酒"], ["创新药", "新能源车"], cons_df)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _constituents_em("创新", "concept")
        assert result["sector"] == "创新药"
        assert result["board_type"] == "concept"
        mock_ak.stock_board_concept_cons_em.assert_called_once_with(symbol="创新药")

    def test_intel_phrase_with_suffix_resolves_board(self):
        """Spec scenario: the intel keyword 「创新药板块」 resolves to the board 创新药."""
        import pandas as pd

        cons_df = pd.DataFrame({"代码": ["600276"], "名称": ["恒瑞医药"]})
        mock_ak = self._mock_ak(["半导体"], ["创新药"], cons_df)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _constituents_em("创新药板块", "concept")
        assert result["sector"] == "创新药"
        mock_ak.stock_board_concept_cons_em.assert_called_once_with(symbol="创新药")

    def test_no_matching_board_raises(self):
        import pandas as pd

        cons_df = pd.DataFrame({"代码": ["600276"], "名称": ["恒瑞医药"]})
        mock_ak = self._mock_ak(["半导体"], ["白酒"], cons_df)
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            pytest.raises(ValueError, match="no eastmoney industry board"),
        ):
            _constituents_em("创新药", "industry")

    def test_empty_constituents_raises(self):
        import pandas as pd

        mock_ak = self._mock_ak(["半导体"], [], pd.DataFrame())
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            pytest.raises(ValueError, match="no constituents"),
        ):
            _constituents_em("半导体", "industry")


class TestConstituentsSina:
    def test_fuzzy_match_resolves_label(self):
        import pandas as pd

        mock_ak = MagicMock()
        mock_ak.stock_sector_spot.return_value = pd.DataFrame(
            {"label": ["new_cxy", "new_bdt"], "板块": ["创新药", "半导体"]}
        )
        mock_ak.stock_sector_detail.return_value = pd.DataFrame(
            {"代码": ["600276", "688235"], "名称": ["恒瑞医药", "百济神州"]}
        )
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _constituents_sina("创新")
        assert result["sector"] == "创新药"
        assert result["source"] == "sina"
        assert result["board_type"] == "industry"
        assert result["count"] == 2
        mock_ak.stock_sector_detail.assert_called_once_with(sector="new_cxy")

    def test_no_match_raises(self):
        import pandas as pd

        mock_ak = MagicMock()
        mock_ak.stock_sector_spot.return_value = pd.DataFrame({"label": ["new_bdt"], "板块": ["半导体"]})
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            pytest.raises(ValueError, match="no sina board"),
        ):
            _constituents_sina("创新药")

    def _mock_ak_multi_indicator(self):
        """Industry indicators hold 酿酒行业; only 概念 holds 白酒概念 (real akshare layout)."""
        import pandas as pd

        mock_ak = MagicMock()

        def spot(indicator):
            if indicator == "概念":
                return pd.DataFrame({"label": ["gn_bjgn"], "板块": ["白酒概念"]})
            return pd.DataFrame({"label": ["new_nyhy"], "板块": ["酿酒行业"]})

        mock_ak.stock_sector_spot.side_effect = spot
        mock_ak.stock_sector_detail.return_value = pd.DataFrame({"代码": ["600519"], "名称": ["贵州茅台"]})
        return mock_ak

    def test_concept_board_type_uses_concept_indicator(self):
        mock_ak = self._mock_ak_multi_indicator()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _constituents_sina("白酒", "concept")
        assert result["sector"] == "白酒概念"
        assert result["board_type"] == "concept"
        assert result["source"] == "sina"
        mock_ak.stock_sector_spot.assert_called_once_with(indicator="概念")
        mock_ak.stock_sector_detail.assert_called_once_with(sector="gn_bjgn")

    def test_auto_falls_through_to_concept_indicator(self):
        """auto tries 新浪行业 → 行业 → 概念 → 地域; 白酒 hits only in 概念."""
        mock_ak = self._mock_ak_multi_indicator()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _constituents_sina("白酒")
        assert result["sector"] == "白酒概念"
        assert result["board_type"] == "concept"
        indicators = [c.kwargs["indicator"] for c in mock_ak.stock_sector_spot.call_args_list]
        assert indicators == ["新浪行业", "行业", "概念"]

    def test_industry_board_type_never_queries_concept_indicator(self):
        mock_ak = self._mock_ak_multi_indicator()
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            pytest.raises(ValueError, match="no sina board"),
        ):
            _constituents_sina("白酒", "industry")
        indicators = [c.kwargs["indicator"] for c in mock_ak.stock_sector_spot.call_args_list]
        assert indicators == ["新浪行业", "行业"]


class TestSectorConstituentsFailover:
    @patch("tools.stock_data._constituents_sina")
    @patch("tools.stock_data._constituents_em")
    def test_em_success_sina_not_called(self, mock_em, mock_sina):
        mock_em.return_value = {"sector": "创新药", "board_type": "concept", "source": "eastmoney"}
        result = sector_constituents_a("创新药", "auto")
        assert result["source"] == "eastmoney"
        mock_sina.assert_not_called()

    @patch("tools.stock_data._constituents_sina")
    @patch("tools.stock_data._constituents_em")
    def test_em_down_sina_used(self, mock_em, mock_sina):
        mock_em.side_effect = ValueError("em down")
        mock_sina.return_value = {"sector": "创新药", "board_type": "industry", "source": "sina"}
        result = sector_constituents_a("创新药", "auto")
        assert result["source"] == "sina"

    @patch("tools.stock_data._constituents_sina")
    @patch("tools.stock_data._constituents_em")
    def test_all_down_raises_last(self, mock_em, mock_sina):
        mock_em.side_effect = ValueError("em down")
        mock_sina.side_effect = ValueError("sina down")
        with pytest.raises(ValueError, match="sina down"):
            sector_constituents_a("创新药", "auto")

    @patch("tools.stock_data._constituents_sina")
    @patch("tools.stock_data._constituents_em")
    def test_board_type_industry_skips_concept(self, mock_em, mock_sina):
        mock_em.return_value = {"sector": "半导体", "board_type": "industry", "source": "eastmoney"}
        sector_constituents_a("半导体", "industry")
        mock_em.assert_called_once_with("半导体", "industry")

    @patch("tools.stock_data._constituents_sina")
    @patch("tools.stock_data._constituents_em")
    def test_board_type_concept_skips_industry(self, mock_em, mock_sina):
        mock_em.return_value = {"sector": "创新药", "board_type": "concept", "source": "eastmoney"}
        sector_constituents_a("创新药", "concept")
        mock_em.assert_called_once_with("创新药", "concept")

    def test_em_down_sina_concept_indicator_hit(self):
        """Smoke scenario: sector_constituents 白酒 --board-type concept with both
        eastmoney sources down must still resolve via sina's 概念 indicator (白酒概念)."""
        import pandas as pd

        mock_ak = MagicMock()

        def spot(indicator):
            if indicator == "概念":
                return pd.DataFrame({"label": ["gn_bjgn"], "板块": ["白酒概念"]})
            return pd.DataFrame({"label": ["new_nyhy"], "板块": ["酿酒行业"]})

        mock_ak.stock_sector_spot.side_effect = spot
        mock_ak.stock_sector_detail.return_value = pd.DataFrame(
            {"代码": ["600519", "000858"], "名称": ["贵州茅台", "五粮液"]}
        )
        with (
            patch("tools.stock_data._constituents_em", side_effect=ValueError("em down")),
            patch.dict(sys.modules, {"akshare": mock_ak}),
        ):
            result = sector_constituents_a("白酒", "concept")
        assert result["source"] == "sina"
        assert result["board_type"] == "concept"
        assert result["sector"] == "白酒概念"
        assert result["count"] == 2


@pytest.fixture
def sector_cache_dir(tmp_path):
    with patch("tools.stock_data._SECTOR_CACHE_DIR", tmp_path):
        yield tmp_path


class TestCmdSectorConstituents:
    def test_success_then_cache_hit(self, sector_cache_dir):
        payload = {
            "sector": "创新药",
            "board_type": "concept",
            "source": "eastmoney",
            "constituents": [{"code": "600276", "name": "恒瑞医药"}],
            "count": 1,
        }
        with patch("tools.stock_data.sector_constituents_a", return_value=payload) as mock_fn:
            args = Namespace(sector="创新药", board_type="auto")
            assert cmd_sector_constituents(args) == payload
            mock_fn.reset_mock()
            assert cmd_sector_constituents(args) == payload
            mock_fn.assert_not_called()

    def test_all_sources_down_returns_error(self, sector_cache_dir):
        with patch("tools.stock_data.sector_constituents_a", side_effect=ValueError("all down")):
            result = cmd_sector_constituents(Namespace(sector="创新药", board_type="auto"))
        assert "error" in result

    def test_error_not_cached(self, sector_cache_dir):
        payload = {"sector": "创新药", "board_type": "concept", "source": "sina", "constituents": [], "count": 0}
        with patch(
            "tools.stock_data.sector_constituents_a",
            side_effect=[ValueError("down"), payload],
        ) as mock_fn:
            args = Namespace(sector="创新药", board_type="auto")
            assert "error" in cmd_sector_constituents(args)
            assert cmd_sector_constituents(args) == payload
            assert mock_fn.call_count == 2

    def test_expired_cache_refetches(self, sector_cache_dir):
        import os
        import time

        payload = {"sector": "创新药", "board_type": "concept", "source": "sina", "constituents": [], "count": 0}
        with patch("tools.stock_data.sector_constituents_a", return_value=payload) as mock_fn:
            args = Namespace(sector="创新药", board_type="auto")
            cmd_sector_constituents(args)
            for f in sector_cache_dir.glob("*.json"):
                old = time.time() - 25 * 3600
                os.utime(f, (old, old))
            mock_fn.reset_mock()
            cmd_sector_constituents(args)
            mock_fn.assert_called_once()

    def test_whitespace_variants_use_distinct_cache_keys(self, sector_cache_dir):
        """「创新药」and「创新 药」must not share a cache file — keys are sha256(sector|board_type),
        not sanitized names, so whitespace/punctuation variants can't collide."""
        payload = {"sector": "创新药", "board_type": "concept", "source": "sina", "constituents": [], "count": 0}
        with patch("tools.stock_data.sector_constituents_a", return_value=payload) as mock_fn:
            cmd_sector_constituents(Namespace(sector="创新药", board_type="auto"))
            cmd_sector_constituents(Namespace(sector="创新 药", board_type="auto"))
            assert mock_fn.call_count == 2


class TestStockBoardsEm:
    def test_returns_industry(self):
        import pandas as pd

        mock_ak = MagicMock()
        mock_ak.stock_individual_info_em.return_value = pd.DataFrame(
            {"item": ["股票简称", "行业"], "value": ["贵州茅台", "酿酒行业"]}
        )
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _stock_boards_em("600519")
        assert result == [{"name": "酿酒行业", "source": "eastmoney", "board_type": "industry"}]

    def test_empty_raises(self):
        import pandas as pd

        mock_ak = MagicMock()
        mock_ak.stock_individual_info_em.return_value = pd.DataFrame()
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            pytest.raises(ValueError, match="unavailable"),
        ):
            _stock_boards_em("600519")


class TestStockBoardsEfinance:
    @patch("efinance.stock.get_belong_board")
    def test_returns_boards(self, mock_board):
        import pandas as pd

        mock_board.return_value = pd.DataFrame({"板块代码": ["BK0477", "BK0896"], "板块名称": ["酿酒行业", "白酒"]})
        result = _stock_boards_efinance("600519")
        assert [s["name"] for s in result] == ["酿酒行业", "白酒"]
        assert all(s["source"] == "efinance" and s["board_type"] == "concept" for s in result)

    @patch("efinance.stock.get_belong_board")
    def test_empty_raises(self, mock_board):
        import pandas as pd

        mock_board.return_value = pd.DataFrame()
        with pytest.raises(ValueError, match="unavailable"):
            _stock_boards_efinance("600519")


class TestXqSymbol:
    def test_shanghai_prefixed(self):
        assert _xq_symbol("600519") == "SH600519"

    def test_shenzhen_prefixed(self):
        assert _xq_symbol("000858") == "SZ000858"

    def test_beijing_prefixed(self):
        assert _xq_symbol("832000") == "BJ832000"


class TestStockBoardsXueqiu:
    """The xueqiu source is strictly env-gated: akshare's built-in xq_a_token is
    stale (xueqiu answers error_code 400016), so no XUEQIU_TOKEN → source skipped."""

    def test_no_token_skips_source(self, monkeypatch):
        monkeypatch.delenv("XUEQIU_TOKEN", raising=False)
        mock_ak = MagicMock()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            assert _stock_boards_xueqiu("600519") == []
        mock_ak.stock_individual_basic_info_xq.assert_not_called()

    def test_with_token_returns_industry(self, monkeypatch):
        import pandas as pd

        monkeypatch.setenv("XUEQIU_TOKEN", "tok123")
        mock_ak = MagicMock()
        mock_ak.stock_individual_basic_info_xq.return_value = pd.DataFrame(
            {
                "item": ["org_short_name", "affiliate_industry"],
                "value": ["比亚迪", {"ind_code": "BK0025", "ind_name": "汽车整车"}],
            }
        )
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = _stock_boards_xueqiu("002594")
        mock_ak.stock_individual_basic_info_xq.assert_called_once_with(symbol="SZ002594", token="tok123")
        assert result == [{"name": "汽车整车", "source": "xueqiu", "board_type": "industry"}]

    def test_affiliate_industry_malformed_raises(self, monkeypatch):
        import pandas as pd

        monkeypatch.setenv("XUEQIU_TOKEN", "tok123")
        mock_ak = MagicMock()
        mock_ak.stock_individual_basic_info_xq.return_value = pd.DataFrame(
            {"item": ["org_short_name", "affiliate_industry"], "value": ["比亚迪", None]}
        )
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            pytest.raises(ValueError, match="no industry"),
        ):
            _stock_boards_xueqiu("002594")

    def test_empty_raises(self, monkeypatch):
        import pandas as pd

        monkeypatch.setenv("XUEQIU_TOKEN", "tok123")
        mock_ak = MagicMock()
        mock_ak.stock_individual_basic_info_xq.return_value = pd.DataFrame()
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            pytest.raises(ValueError, match="unavailable"),
        ):
            _stock_boards_xueqiu("002594")


class TestStockBoardsFromCache:
    """The cache reverse lookup scans the sector_constituents disk cache — the last
    fallback when eastmoney blocks the caller's IP and xueqiu has no token."""

    def _payload(self, sector="创新药", board_type="concept", codes=("600276", "688235")):
        return {
            "sector": sector,
            "board_type": board_type,
            "source": "eastmoney",
            "constituents": [{"code": c, "name": "x"} for c in codes],
            "count": len(codes),
        }

    def test_cold_cache_returns_empty(self, sector_cache_dir):
        assert _stock_boards_from_cache("600276") == []

    def test_hit_returns_board_entry(self, sector_cache_dir):
        _sector_cache_set("k1", self._payload())
        assert _stock_boards_from_cache("600276") == [{"name": "创新药", "source": "cache", "board_type": "concept"}]

    def test_symbol_in_multiple_cached_sectors(self, sector_cache_dir):
        _sector_cache_set("k1", self._payload())
        _sector_cache_set("k2", self._payload(sector="医药生物", board_type="industry"))
        names = {b["name"] for b in _stock_boards_from_cache("600276")}
        assert names == {"创新药", "医药生物"}

    def test_symbol_not_in_constituents_returns_empty(self, sector_cache_dir):
        _sector_cache_set("k1", self._payload())
        assert _stock_boards_from_cache("000858") == []

    def test_expired_cache_ignored(self, sector_cache_dir):
        import os
        import time

        _sector_cache_set("k1", self._payload())
        for f in sector_cache_dir.glob("*.json"):
            old = time.time() - 25 * 3600
            os.utime(f, (old, old))
        assert _stock_boards_from_cache("600276") == []

    def test_bad_json_tolerated(self, sector_cache_dir):
        (sector_cache_dir / "corrupt.json").write_text("not json", encoding="utf-8")
        (sector_cache_dir / "non-dict.json").write_text("[1, 2]", encoding="utf-8")
        _sector_cache_set("k1", self._payload())
        assert _stock_boards_from_cache("600276")[0]["name"] == "创新药"


class TestResolveStockSectors:
    # _stock_boards_xueqiu is patched to [] in every A-share test so a dev machine
    # with XUEQIU_TOKEN set doesn't silently make a real network call; same for
    # _stock_boards_from_cache vs a warm sector cache in the real tempdir.
    @patch("tools.stock_data._stock_boards_from_cache", return_value=[])
    @patch("tools.stock_data._stock_boards_xueqiu", return_value=[])
    @patch("tools.stock_data._stock_boards_efinance")
    @patch("tools.stock_data._stock_boards_em")
    def test_a_share_merges_and_dedupes(self, mock_em, mock_ef, _mock_xq, _mock_cache):
        mock_em.return_value = [{"name": "酿酒行业", "source": "eastmoney", "board_type": "industry"}]
        mock_ef.return_value = [
            {"name": "酿酒行业", "source": "efinance", "board_type": "concept"},
            {"name": "白酒", "source": "efinance", "board_type": "concept"},
        ]
        result = resolve_stock_sectors("600519")
        assert result["symbol"] == "600519"
        assert result["market"] == "A"
        names = [s["name"] for s in result["sectors"]]
        assert names == ["酿酒行业", "白酒"]
        assert result["sectors"][0]["source"] == "eastmoney"

    @patch("tools.stock_data._stock_boards_from_cache", return_value=[])
    @patch("tools.stock_data._stock_boards_xueqiu", return_value=[])
    @patch("tools.stock_data._stock_boards_efinance")
    @patch("tools.stock_data._stock_boards_em")
    def test_a_share_em_down_efinance_degrades(self, mock_em, mock_ef, _mock_xq, _mock_cache):
        mock_em.side_effect = ValueError("em down")
        mock_ef.return_value = [{"name": "白酒", "source": "efinance", "board_type": "concept"}]
        result = resolve_stock_sectors("600519")
        assert "error" not in result
        assert [s["name"] for s in result["sectors"]] == ["白酒"]

    @patch("tools.stock_data._stock_boards_from_cache", return_value=[])
    @patch("tools.stock_data._stock_boards_xueqiu")
    @patch("tools.stock_data._stock_boards_efinance")
    @patch("tools.stock_data._stock_boards_em")
    def test_a_share_eastmoney_sources_down_xueqiu_degrades(self, mock_em, mock_ef, mock_xq, _mock_cache):
        """xueqiu is the non-eastmoney fallback: em + efinance both down still yields boards."""
        mock_em.side_effect = ValueError("em down")
        mock_ef.side_effect = ValueError("ef down")
        mock_xq.return_value = [{"name": "汽车整车", "source": "xueqiu", "board_type": "industry"}]
        result = resolve_stock_sectors("002594")
        assert "error" not in result
        assert result["sectors"] == [{"name": "汽车整车", "source": "xueqiu", "board_type": "industry"}]

    @patch("tools.stock_data._stock_boards_from_cache", return_value=[])
    @patch("tools.stock_data._stock_boards_xueqiu", return_value=[])
    @patch("tools.stock_data._stock_boards_efinance")
    @patch("tools.stock_data._stock_boards_em")
    def test_a_share_both_down_returns_error(self, mock_em, mock_ef, _mock_xq, _mock_cache):
        mock_em.side_effect = ValueError("em down")
        mock_ef.side_effect = ValueError("ef down")
        result = resolve_stock_sectors("600519")
        assert "error" in result
        assert "sectors" not in result
        # all live sources empty + cold cache → the error must tell the user how to recover
        assert "get_sector_constituents" in result["error"]
        assert "XUEQIU_TOKEN" in result["error"]

    @patch("tools.stock_data._stock_boards_from_cache")
    @patch("tools.stock_data._stock_boards_xueqiu", return_value=[])
    @patch("tools.stock_data._stock_boards_efinance")
    @patch("tools.stock_data._stock_boards_em")
    def test_a_share_live_sources_down_cache_degrades(self, mock_em, mock_ef, _mock_xq, mock_cache):
        """The cache reverse lookup is the last resort when eastmoney blocks the IP."""
        mock_em.side_effect = ValueError("em down")
        mock_ef.side_effect = ValueError("ef down")
        mock_cache.return_value = [{"name": "酿酒行业", "source": "cache", "board_type": "industry"}]
        result = resolve_stock_sectors("600519")
        assert "error" not in result
        assert result["sectors"] == [{"name": "酿酒行业", "source": "cache", "board_type": "industry"}]

    def test_hk_returns_gics(self, mock_yfinance):
        mock_ticker = MagicMock()
        mock_yfinance.Ticker.return_value = mock_ticker
        mock_ticker.info = {"sector": "Technology", "industry": "Consumer Electronics"}
        result = resolve_stock_sectors("00700.HK")
        assert result["market"] == "HK"
        names = {s["name"] for s in result["sectors"]}
        assert names == {"Technology", "Consumer Electronics"}
        assert all(s["source"] == "yfinance" and s["board_type"] == "gics" for s in result["sectors"])

    def test_hk_no_info_returns_error(self, mock_yfinance):
        mock_ticker = MagicMock()
        mock_yfinance.Ticker.return_value = mock_ticker
        mock_ticker.info = {}
        result = resolve_stock_sectors("00700.HK")
        assert "error" in result

    def test_unsupported_market_returns_error(self):
        result = resolve_stock_sectors("AAPL")
        assert result["market"] == "US"
        assert "error" in result

    def test_hk_leading_zero_stripped_for_yfinance(self, mock_yfinance):
        """Yahoo 404s on 5-digit HK codes: 01801.HK must be queried as 1801.HK,
        while the returned symbol keeps the user's original input."""
        mock_ticker = MagicMock()
        mock_yfinance.Ticker.return_value = mock_ticker
        mock_ticker.info = {"sector": "Health Care", "industry": "Biotechnology"}
        result = resolve_stock_sectors("01801.HK")
        mock_yfinance.Ticker.assert_called_once_with("1801.HK")
        assert result["symbol"] == "01801.HK"
        assert result["market"] == "HK"
        assert {s["name"] for s in result["sectors"]} == {"Health Care", "Biotechnology"}


class TestYfHkSymbol:
    def test_five_digit_leading_zero_stripped(self):
        assert _yf_hk_symbol("01801.HK") == "1801.HK"

    def test_four_digit_preserved(self):
        assert _yf_hk_symbol("0700.HK") == "0700.HK"

    def test_short_code_padded(self):
        assert _yf_hk_symbol("5.HK") == "0005.HK"

    def test_lowercase_suffix_normalized(self):
        assert _yf_hk_symbol("01801.hk") == "1801.HK"

    def test_non_hk_unchanged(self):
        assert _yf_hk_symbol("AAPL") == "AAPL"


class TestYfHkNormalizationApplied:
    """Every yfinance call site must route HK symbols through _yf_hk_symbol:
    Yahoo 404s on 5-digit HK codes (01801.HK), while 4-digit codes (0700.HK)
    must NOT be rewritten."""

    def _kline_df(self):
        import pandas as pd

        return pd.DataFrame(
            {
                "Date": pd.date_range("2026-01-01", periods=2),
                "Open": [10.0, 11.0],
                "High": [11.0, 12.0],
                "Low": [9.0, 10.0],
                "Close": [10.5, 11.5],
                "Volume": [1000, 2000],
            }
        )

    def test_quote_strips_leading_zero(self, mock_yfinance):
        mock_yfinance.Ticker.return_value.info = {"regularMarketPrice": 10.0}
        result = _quote_yfinance("01801.HK")
        mock_yfinance.Ticker.assert_called_with("1801.HK")
        assert result["symbol"] == "01801.HK"  # caller keeps the user's original symbol

    def test_quote_keeps_four_digit_code(self, mock_yfinance):
        mock_yfinance.Ticker.return_value.info = {"regularMarketPrice": 500.0}
        _quote_yfinance("0700.HK")
        mock_yfinance.Ticker.assert_called_with("0700.HK")

    def test_kline_strips_leading_zero(self, mock_yfinance):
        mock_yfinance.download.return_value = self._kline_df()
        _kline_yfinance("01801.HK", "daily", 2)
        assert mock_yfinance.download.call_args[0][0] == "1801.HK"

    def test_kline_keeps_four_digit_code(self, mock_yfinance):
        mock_yfinance.download.return_value = self._kline_df()
        _kline_yfinance("0700.HK", "daily", 2)
        assert mock_yfinance.download.call_args[0][0] == "0700.HK"

    def test_financials_strips_leading_zero(self, mock_yfinance):
        mock_yfinance.Ticker.return_value.info = {"shortName": "Innovent"}
        financials_yf("01801.HK")
        mock_yfinance.Ticker.assert_called_with("1801.HK")

    def test_stock_info_strips_leading_zero(self, mock_yfinance):
        mock_yfinance.Ticker.return_value.info = {"shortName": "Innovent"}
        cmd_stock_info(Namespace(symbol="01801.HK"))
        mock_yfinance.Ticker.assert_called_with("1801.HK")

    def test_news_strips_leading_zero(self, mock_yfinance):
        mock_yfinance.Ticker.return_value.news = [
            {"title": "t", "publisher": "p", "link": "u", "providerPublishTime": 1, "type": "STORY"}
        ]
        result = cmd_news(Namespace(symbol="01801.HK", days=3))
        mock_yfinance.Ticker.assert_called_with("1801.HK")
        assert result[0]["title"] == "t"


class TestStockSectorsHkRetry:
    """HK lookups ride the shared _akshare_retry policy (2 retries, 1s delay) —
    one integration-style check that a transient Yahoo failure recovers."""

    def test_first_failure_retries_and_succeeds(self, mock_yfinance):
        mock_ticker = MagicMock()
        mock_ticker.info = {"sector": "Technology", "industry": "Consumer Electronics"}
        mock_yfinance.Ticker.side_effect = [ConnectionError("boom"), mock_ticker]
        with patch("tools.stock_data.time.sleep") as mock_sleep:
            result = _stock_sectors_hk("00700.HK")
        assert mock_yfinance.Ticker.call_count == 2
        mock_sleep.assert_called_once_with(1)
        assert {s["name"] for s in result} == {"Technology", "Consumer Electronics"}


class TestCmdStockInfoBoards:
    """Regression: boards was dead code — stock_board_industry_cons_em was called with a
    stock code instead of a board name and the exception was swallowed forever."""

    def _info_df(self):
        import pandas as pd

        return pd.DataFrame({"item": ["股票简称", "行业", "上市时间"], "value": ["贵州茅台", "酿酒行业", "2001-08-27"]})

    def test_boards_filled_from_resolve(self):
        mock_ak = MagicMock()
        mock_ak.stock_individual_info_em.return_value = self._info_df()
        mock_ak.stock_board_industry_cons_em.side_effect = AssertionError(
            "stock_board_industry_cons_em must not be called with a stock code"
        )
        resolved = {
            "symbol": "600519",
            "market": "A",
            "sectors": [
                {"name": "酿酒行业", "source": "eastmoney", "board_type": "industry"},
                {"name": "白酒", "source": "efinance", "board_type": "concept"},
            ],
        }
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            patch("tools.stock_data.resolve_stock_sectors", return_value=resolved),
        ):
            result = cmd_stock_info(Namespace(symbol="600519"))
        assert result["boards"] == ["酿酒行业", "白酒"]
        assert result["industry"] == "酿酒行业"

    def test_boards_absent_when_resolve_fails(self):
        mock_ak = MagicMock()
        mock_ak.stock_individual_info_em.return_value = self._info_df()
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            patch("tools.stock_data.resolve_stock_sectors", side_effect=Exception("all sources down")),
        ):
            result = cmd_stock_info(Namespace(symbol="600519"))
        assert "boards" not in result
        assert result["industry"] == "酿酒行业"
