"""A-share K-line prices must not change adjustment basis during failover."""

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from tools import stock_data
from tools.stock_data import _kline_tencent


@pytest.fixture(autouse=True)
def isolate_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(stock_data, "_DATA_CACHE_DIR", tmp_path)
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    for name in ("akshare", "efinance", "tencent", "sina", "baostock"):
        monkeypatch.setattr(stock_data, f"_kline_{name}", MagicMock(side_effect=ValueError("offline")))


def response(fields, items):
    return MagicMock(json=lambda: {"code": 0, "data": {"fields": fields, "items": items}})


def test_daily_failover_adjusts_all_prices_before_returning(monkeypatch):
    def post(_url, *, json, timeout):
        if json["api_name"] == "adj_factor":
            return response(["trade_date", "adj_factor"], [["20260519", 2], ["20260518", 1]])
        return response(
            ["trade_date", "open", "high", "low", "close", "vol", "amount", "pct_chg"],
            [["20260519", 10, 12, 9, 11, 1000, 11000, 10], ["20260518", 20, 24, 18, 22, 900, 19800, 0]],
        )

    monkeypatch.setattr("requests.post", post)
    rows = stock_data.cmd_kline(Namespace(symbol="600519", period="daily", count=2))
    assert [(row["open"], row["high"], row["low"], row["close"]) for row in rows] == [
        (10, 12, 9, 11),
        (10, 12, 9, 11),
    ]
    assert [row["volume"] for row in rows] == [900, 1000]
    assert [row["turnover"] for row in rows] == [19800, 11000]
    assert all(row["adjusted"] == "qfq" for row in rows)


@pytest.mark.parametrize("period,freq", [("weekly", "week"), ("monthly", "month")])
def test_week_month_uses_adjusted_ohlc(monkeypatch, period, freq):
    calls = []

    def post(_url, *, json, timeout):
        calls.append(json)
        return response(
            ["trade_date", "open_qfq", "high_qfq", "low_qfq", "close_qfq", "vol", "amount", "pct_chg"],
            [["20260522", 10, 12, 9, 11, 1000, 11000, 10]],
        )

    monkeypatch.setattr("requests.post", post)
    rows = stock_data.cmd_kline(Namespace(symbol="600519", period=period, count=1))
    assert rows[0]["close"] == 11
    assert rows[0]["adjusted"] == "qfq"
    assert calls[0]["api_name"] == "stk_week_month_adj"
    assert calls[0]["params"]["freq"] == freq


@pytest.mark.parametrize("symbol,ts_code", [("600519", "600519.SH"), ("900901", "900901.SH"), ("920001", "920001.BJ")])
def test_tushare_preserves_exchange_mapping(monkeypatch, symbol, ts_code):
    calls = []

    def post(_url, *, json, timeout):
        calls.append(json)
        return response(
            ["trade_date", "open_qfq", "high_qfq", "low_qfq", "close_qfq", "vol"],
            [["20260522", 10, 12, 9, 11, 900]],
        )

    monkeypatch.setattr("requests.post", post)
    rows = stock_data.cmd_kline(Namespace(symbol=symbol, period="weekly", count=1))
    assert rows[0]["close"] == 11
    assert calls[0]["params"]["ts_code"] == ts_code


def test_tencent_index_selects_raw_points_when_both_series_exist(monkeypatch):
    raw = [["2026-05-18", "3000", "3100", "3200", "2900", "900"]]
    adjusted = [["2026-05-18", "30", "31", "32", "29", "900"]]
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: MagicMock(
            json=lambda: {
                "code": 0,
                "data": {"sh000001": {"day": raw, "qfqday": adjusted}},
            }
        ),
    )
    assert _kline_tencent("sh000001", "daily", 1)[0]["close"] == 3100


def test_weekly_raw_only_response_is_not_accepted(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: response(
            ["trade_date", "open", "high", "low", "close", "vol"],
            [["20260522", 20, 24, 18, 22, 900]],
        ),
    )
    rows = stock_data.cmd_kline(Namespace(symbol="600519", period="weekly", count=1))
    assert "error" in rows


@pytest.mark.parametrize("price", ["invalid", float("inf")])
def test_invalid_adjusted_prices_fail_over(monkeypatch, price):
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: response(
            ["trade_date", "open_qfq", "high_qfq", "low_qfq", "close_qfq", "vol"],
            [["20260522", 10, 12, 9, price, 900]],
        ),
    )
    rows = stock_data.cmd_kline(Namespace(symbol="600519", period="weekly", count=1))
    assert "error" in rows


@pytest.mark.parametrize(
    "factor_rows",
    [[], [["20260519", 1]], [["20260518", 0]], [["20260518", -1]], [["20260518", None]], [["20260518", float("inf")]]],
)
def test_missing_or_invalid_factors_fail_over_without_using_raw_prices(monkeypatch, factor_rows):
    def post(_url, *, json, timeout):
        if json["api_name"] == "adj_factor":
            return response(["trade_date", "adj_factor"], factor_rows)
        return response(
            ["trade_date", "open", "high", "low", "close", "vol"],
            [["20260518", 20, 24, 18, 22, 900]],
        )

    monkeypatch.setattr("requests.post", post)
    stock_data._kline_baostock.return_value = [{"close": 11}]
    stock_data._kline_baostock.side_effect = None
    stock_data._kline_sina.side_effect = None
    stock_data._kline_sina.return_value = [{"close": 22}]
    rows = stock_data.cmd_kline(Namespace(symbol="600519", period="daily", count=1))
    assert rows == [{"close": 11, "adjusted": "qfq"}]
    stock_data._kline_sina.assert_not_called()
    # The winning adjusted source stays preferred on the next call.
    assert stock_data.cmd_kline(Namespace(symbol="600519", period="daily", count=1)) == rows
    assert stock_data._kline_baostock.call_count == 2


@pytest.mark.parametrize("symbol", ["600519", "sh600519", "510300", "920001", "bj920001"])
def test_raw_tencent_stock_series_is_rejected(monkeypatch, symbol):
    code = stock_data._cn_code(symbol)
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: MagicMock(
            json=lambda: {
                "code": 0,
                "data": {code: {"day": [["2026-05-18", "20", "22", "24", "18", "900"]]}},
            }
        ),
    )
    with pytest.raises(ValueError, match="required adjustment"):
        _kline_tencent(symbol, "daily", 1)


@pytest.mark.parametrize("symbol", ["sh000001", "sz399006", "SH000300"])
def test_index_keeps_raw_points_and_reports_adjustment(monkeypatch, symbol):
    stock_data._kline_tencent.side_effect = None
    stock_data._kline_tencent.return_value = [{"close": 3000}]
    rows = stock_data.cmd_kline(Namespace(symbol=symbol, period="daily", count=1))
    assert rows == [{"close": 3000, "adjusted": "none"}]


def test_all_adjusted_sources_down_returns_json_error(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN")
    stock_data._kline_sina.side_effect = None
    stock_data._kline_sina.return_value = [{"close": 22}]
    rows = stock_data.cmd_kline(Namespace(symbol="600519", period="daily", count=1))
    assert "error" in rows
    stock_data._kline_sina.assert_not_called()


def test_prefixed_stock_uses_stock_adjustment_chain():
    stock_data._kline_akshare.side_effect = None
    stock_data._kline_akshare.return_value = [{"close": 11}]
    rows = stock_data.cmd_kline(Namespace(symbol="sh600519", period="daily", count=1))
    assert rows == [{"close": 11, "adjusted": "qfq"}]
    stock_data._kline_akshare.assert_called_once_with("600519", "daily", 1)


def test_etf_skips_tushare_stock_prices(monkeypatch):
    post = MagicMock()
    monkeypatch.setattr("requests.post", post)
    stock_data._kline_efinance.side_effect = None
    stock_data._kline_efinance.return_value = [{"close": 3}]
    rows = stock_data.cmd_kline(Namespace(symbol="510300", period="daily", count=1))
    assert rows == [{"close": 3, "adjusted": "qfq"}]
    post.assert_not_called()
