"""Tests for failover logic in tools/stock_data.py."""

import json
import subprocess
import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from tools.stock_data import (
    _capital_flow_efinance,
    _failover,
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
    _sector_rankings_efinance,
    cmd_capital_flow,
    cmd_news,
    cmd_sector_rankings,
    kline_a,
    kline_yf,
    quote_a,
    quote_yf,
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
    def test_returns_data(self):
        mock_api_cls = MagicMock()
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.connect.return_value.__enter__ = MagicMock(return_value=mock_api)
        mock_api.connect.return_value.__exit__ = MagicMock(return_value=False)
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

    def test_empty_raises(self):
        mock_api_cls = MagicMock()
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.connect.return_value.__enter__ = MagicMock(return_value=mock_api)
        mock_api.connect.return_value.__exit__ = MagicMock(return_value=False)
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
        mock_api.connect.return_value.__enter__ = MagicMock(return_value=mock_api)
        mock_api.connect.return_value.__exit__ = MagicMock(return_value=False)
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
