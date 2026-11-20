from tools.stock_data import calc_limit_price, detect_market, normalize_stock_code


class TestDetectMarket:
    def test_a_share_6_digit(self):
        assert detect_market("600519") == "A"
        assert detect_market("000001") == "A"
        assert detect_market("300750") == "A"
        assert detect_market("688981") == "A"

    def test_a_share_explicit_exchange_prefix(self):
        assert detect_market("sh000001") == "A"
        assert detect_market("SZ399006") == "A"

    def test_hk(self):
        assert detect_market("0700.HK") == "HK"
        assert detect_market("9988.hk") == "HK"

    def test_us(self):
        assert detect_market("AAPL") == "US"
        assert detect_market("TSLA") == "US"
        assert detect_market("MSFT") == "US"

    def test_jp(self):
        assert detect_market("7203.T") == "JP"
        assert detect_market("6758.t") == "JP"

    def test_kr(self):
        assert detect_market("005930.KS") == "KR"
        assert detect_market("035720.KQ") == "KR"

    def test_tw(self):
        assert detect_market("2330.TW") == "TW"
        assert detect_market("6510.TWO") == "TW"


class TestNormalizeStockCode:
    def test_main_board(self):
        info = normalize_stock_code("600519")
        assert info["market"] == "A"
        assert info["board"] == "main"
        assert info["limit_pct"] == 0.10

    def test_star_board(self):
        info = normalize_stock_code("688981")
        assert info["board"] == "STAR"
        assert info["limit_pct"] == 0.20

    def test_chinext(self):
        info = normalize_stock_code("300750")
        assert info["board"] == "ChiNext"
        assert info["limit_pct"] == 0.20

    def test_bse(self):
        info = normalize_stock_code("430047")
        assert info["board"] == "BSE"
        assert info["limit_pct"] == 0.30

    def test_etf(self):
        info = normalize_stock_code("510300")
        assert info["board"] == "ETF"
        assert info["is_etf"] is True
        assert info["limit_pct"] is None

    def test_us_no_limit(self):
        info = normalize_stock_code("AAPL")
        assert info["market"] == "US"
        assert info["limit_pct"] is None

    def test_hk_no_limit(self):
        info = normalize_stock_code("0700.HK")
        assert info["market"] == "HK"
        assert info["limit_pct"] is None


class TestCalcLimitPrice:
    def test_limit_up_10pct(self):
        result = calc_limit_price(10.0, 0.10, "up")
        assert abs(result - 11.0) < 0.01

    def test_limit_down_10pct(self):
        result = calc_limit_price(10.0, 0.10, "down")
        assert abs(result - 9.0) < 0.01

    def test_limit_up_20pct(self):
        result = calc_limit_price(10.0, 0.20, "up")
        assert abs(result - 12.0) < 0.01

    def test_limit_down_20pct(self):
        result = calc_limit_price(10.0, 0.20, "down")
        assert abs(result - 8.0) < 0.01

    def test_exchange_rounding(self):
        result = calc_limit_price(13.57, 0.10, "up")
        assert abs(result - 14.93) < 0.01

    def test_exact_half_cent_uses_decimal_half_up(self):
        assert calc_limit_price(1.15, 0.10, "up") == 1.27
        assert calc_limit_price(1.45, 0.30, "down") == 1.02


class TestDetectMarketEdgeCases:
    def test_empty_string_falls_back_to_us(self):
        assert detect_market("") == "US"

    def test_bse_codes_are_a_share(self):
        assert detect_market("830799") == "A"
        assert detect_market("430047") == "A"
        assert detect_market("920001") == "A"

    def test_prefixed_codes(self):
        assert detect_market("sh600519") == "A"
        assert detect_market("bj920001") == "A"


class TestNormalizeStockCodeEdgeCases:
    def test_empty_string(self):
        info = normalize_stock_code("")
        assert info["market"] == "US"  # detect_market fallback for unrecognized input
        assert info["limit_pct"] is None

    def test_bse_8_prefix(self):
        info = normalize_stock_code("830799")
        assert info["board"] == "BSE"
        assert info["limit_pct"] == 0.30

    def test_bse_92_prefix(self):
        info = normalize_stock_code("920001")
        assert info["board"] == "BSE"
        assert info["limit_pct"] == 0.30

    def test_sh_prefixed_code(self):
        info = normalize_stock_code("sh600519")
        assert info["market"] == "A"
        assert info["board"] == "main"
        assert info["limit_pct"] == 0.10


class TestCalcLimitPriceEdgeCases:
    def test_unknown_direction_treated_as_down(self):
        result = calc_limit_price(10.0, 0.10, "sideways")
        assert abs(result - 9.0) < 0.01

    def test_zero_price(self):
        assert calc_limit_price(0.0, 0.10, "up") == 0.0

    def test_negative_price(self):
        assert calc_limit_price(-10.0, 0.10, "up") == -11.0

    def test_zero_ratio_keeps_price(self):
        assert calc_limit_price(10.0, 0.0, "up") == 10.0


def test_financials_annual_history_is_newest_first(monkeypatch):
    from argparse import Namespace

    import pandas as pd

    from tools import stock_data

    rows = pd.DataFrame(
        {
            "日期": ["2020-12-31", "2024-12-31", "2024-09-30", "2023-12-31"],
            "净资产收益率(%)": [1, 4, 3, 2],
            "销售毛利率(%)": [10, 40, 30, 20],
            "销售净利率(%)": [5, 20, 15, 10],
        }
    )
    monkeypatch.setattr(stock_data, "_akshare_retry", lambda *a, **k: rows)
    result = stock_data.cmd_financials(Namespace(symbol="600519", periods=3, annual=True))
    assert result["report_date"] == "2024-12-31"
    assert [r["roe"] for r in result["periods"]] == [4, 2, 1]


def test_financial_cache_persists_provider_date_objects(monkeypatch):
    from argparse import Namespace
    from datetime import date
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    fetch = MagicMock(return_value=pd.DataFrame({"日期": [date(2025, 12, 31)], "净资产收益率(%)": [15]}))
    monkeypatch.setattr(stock_data, "_akshare_retry", fetch)
    args = Namespace(symbol="600519", periods=1, annual=False)
    stock_data.cmd_financials(args)
    result = stock_data.cmd_financials(args)
    assert fetch.call_count == 1
    assert result["report_date"] == "2025-12-31"


def test_basic_info_partial_results_are_not_cached(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    from tools import stock_data

    fetch = MagicMock(side_effect=ValueError("down"))
    monkeypatch.setattr(stock_data, "_akshare_retry", fetch)
    monkeypatch.setattr(stock_data, "resolve_stock_sectors", lambda *a, **k: {"error": "down"})
    for _ in range(2):
        result = stock_data.cmd_stock_info(Namespace(symbol="600519"))
        assert result.get("warnings") or result.get("error")
    assert fetch.call_count == 2


def test_hk_annual_source_failure_keeps_available_yahoo_data(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    ticker = MagicMock()
    ticker.info = {"shortName": "Tencent"}
    ticker.financials = pd.DataFrame({"2025-12-31": [100, 20]}, index=["Total Revenue", "Net Income"])
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    monkeypatch.setattr(stock_data, "_akshare_retry", MagicMock(side_effect=ValueError("down")))
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert result["name"] == "Tencent"
    assert result["periods"][0]["net_income"] == 20
    assert result["warnings"]


def test_non_a_history_derives_margins_from_available_income_rows(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    ticker = MagicMock()
    ticker.info = {"shortName": "Example"}
    ticker.financials = pd.DataFrame(
        {"2025-12-31": [100, 20, 40]}, index=["Total Revenue", "Net Income", "Gross Profit"]
    )
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    result = stock_data.cmd_financials(Namespace(symbol="AAPL", periods=2, annual=False))
    assert result["periods"][0]["net_profit_margin"] == 20
    assert result["periods"][0]["gross_margin"] == 40


def test_sector_constituents_cache_only_in_user_directory(monkeypatch, tmp_path):
    from argparse import Namespace

    from tools import stock_data

    monkeypatch.setattr(stock_data, "_DATA_CACHE_DIR", tmp_path / "state")
    monkeypatch.setattr(
        stock_data,
        "sector_constituents_a",
        lambda *a, **k: {"sector": "白酒", "board_type": "industry", "constituents": [{"code": "600519"}]},
    )
    stock_data.cmd_sector_constituents(Namespace(sector="白酒", board_type="industry"))
    assert not (tmp_path / "state").exists()
    assert stock_data._stock_boards_from_cache("600519") == [
        {"name": "白酒", "source": "cache", "board_type": "industry"}
    ]


def test_hk_financial_backend_uses_five_digits(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    ticker = MagicMock()
    ticker.info = {"shortName": "Tencent"}
    ticker.financials = pd.DataFrame()
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    calls = []

    def fetch(fn, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            {"REPORT_DATE": ["2025-12-31"], "ROE_AVG": [15], "GROSS_PROFIT_RATIO": [40], "NET_PROFIT_RATIO": [20]}
        )

    monkeypatch.setattr(stock_data, "_akshare_retry", fetch)
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert calls[0]["symbol"] == "00700"
    assert result["periods"][0]["roe"] == 15


def test_hk_annual_history_survives_yahoo_snapshot_failure(monkeypatch):
    from argparse import Namespace

    import pandas as pd

    from tools import stock_data

    class Ticker:
        @property
        def info(self):
            raise ValueError("snapshot down")

        financials = pd.DataFrame()

    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: Ticker())
    monkeypatch.setattr(
        stock_data, "_akshare_retry", lambda *a, **k: pd.DataFrame({"REPORT_DATE": ["2025-12-31"], "ROE_AVG": [15]})
    )
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert result["periods"][0]["roe"] == 15
    assert result["warnings"]


def test_quote_and_kline_failover_carry_provider_evidence():
    from tools.stock_data import _failover

    quote = _failover([("unavailable", lambda: None), ("fixture", lambda: {"price": 10})], "quote:AAPL", evidence=True)
    assert quote["source"] == "fixture"
    assert quote["data_as_of"] is None
    assert quote["fetched_at"].endswith("+00:00")
    bars = _failover([("yfinance", lambda: [{"date": "2024-01-02", "close": 10}])], "kline:AAPL", evidence=True)
    assert bars[0]["source"] == "yfinance"
    assert bars[0]["data_as_of"] == "2024-01-02"
    assert bars[0]["adjusted"] == "total_return"


def test_provider_timestamp_can_reveal_stale_quote_during_live_session(monkeypatch):
    from argparse import Namespace

    from tools import stock_data

    monkeypatch.setattr(
        stock_data, "quote_a", lambda *args, **kwargs: {"price": 10, "data_as_of": "2000-01-01T15:00:00+08:00"}
    )
    monkeypatch.setattr(stock_data, "_quote_stale_marker", lambda market: None)
    result = stock_data.cmd_quote(Namespace(symbol="600519"))
    assert result["stale"] is True
    assert result["as_of_basis"] == "provider"
    assert result["as_of"] == "2000-01-01T15:00:00+08:00"


def test_yahoo_quote_preserves_time_from_same_info_response(monkeypatch):
    from argparse import Namespace
    from types import SimpleNamespace

    from tools import stock_data

    calls = []

    def ticker(symbol):
        calls.append(symbol)
        return SimpleNamespace(
            info={"shortName": "Fixture", "regularMarketPrice": 100, "regularMarketTime": 1704205800}
        )

    monkeypatch.setattr("yfinance.Ticker", ticker)
    monkeypatch.setattr(stock_data, "_quote_stale_marker", lambda market: None)
    quote = stock_data.cmd_quote(Namespace(symbol="AAPL"))
    assert quote["data_as_of"] == "2024-01-02T14:30:00+00:00"
    assert quote["as_of_basis"] == "provider"
    assert calls == ["AAPL"]


def test_hk_financials_add_dcf_inputs_from_yahoo_statements(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    period = "2025-12-31"
    ticker = MagicMock()
    ticker.info = {"shortName": "Tencent", "financialCurrency": "CNY"}
    ticker.financials = pd.DataFrame()
    ticker.income_stmt = pd.DataFrame(
        {period: [100, 30, 25, 5, 9]},
        index=["Total Revenue", "Operating Income", "Pretax Income", "Tax Provision", "Diluted Average Shares"],
    )
    ticker.cashflow = pd.DataFrame(
        {period: [40, -12, 7]}, index=["Operating Cash Flow", "Capital Expenditure", "Depreciation And Amortization"]
    )
    ticker.balance_sheet = pd.DataFrame(
        {period: [20, 58, 10, 40, 8, 3, 8]},
        index=[
            "Cash And Cash Equivalents",
            "Total Debt",
            "Current Debt",
            "Long Term Debt",
            "Capital Lease Obligations",
            "Minority Interest",
            "Ordinary Shares Number",
        ],
    )
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    monkeypatch.setattr(stock_data, "_akshare_retry", MagicMock(side_effect=ValueError("down")))
    monkeypatch.delenv("LIXINGER_TOKEN", raising=False)
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=1, annual=True, include_dcf_inputs=True))
    row = result["dcf_inputs"]["periods"][0]
    assert result["dcf_inputs"]["source"] == "yfinance"
    assert row["currency"] == "CNY"
    assert row["capital_expenditure"] == 12
    assert row["effective_tax_rate"] == 0.2
    assert row["minority_interest"] == 3
    assert row["total_debt"] == 50
    assert row["lease_liabilities"] == 8
    assert row["missing_fields"] == []


def test_us_dcf_inputs_fall_back_to_yahoo_when_sec_unavailable(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    import pandas as pd
    import sec_edgar

    from tools import stock_data

    ticker = MagicMock()
    ticker.info = {"shortName": "Example", "financialCurrency": "USD"}
    ticker.financials = pd.DataFrame()
    ticker.income_stmt = pd.DataFrame({"2025-12-31": [100]}, index=["Total Revenue"])
    ticker.cashflow = pd.DataFrame()
    ticker.balance_sheet = pd.DataFrame()
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    monkeypatch.setattr(sec_edgar, "dcf_inputs", MagicMock(side_effect=ValueError("SEC down")))
    result = stock_data.cmd_financials(Namespace(symbol="AAPL", periods=1, annual=False, include_dcf_inputs=True))
    assert result["dcf_inputs"]["source"] == "yfinance"
    assert result["dcf_inputs"]["periods"][0]["revenue"] == 100
    assert any("SEC" in warning for warning in result["dcf_inputs"]["warnings"])


def _yahoo_dcf_ticker(currency="CNY"):
    from unittest.mock import MagicMock

    import pandas as pd

    ticker = MagicMock()
    ticker.info = {"shortName": "Example", "financialCurrency": currency}
    ticker.income_stmt = pd.DataFrame({"2025-12-31": [999, 30]}, index=["Total Revenue", "Operating Income"])
    ticker.cashflow = pd.DataFrame({"2025-12-31": [-12]}, index=["Capital Expenditure"])
    ticker.balance_sheet = pd.DataFrame()
    return ticker


def test_hk_dcf_inputs_prefer_lixinger_and_fill_nulls_from_yahoo(monkeypatch):
    import lixinger

    from tools import stock_data

    primary = {
        "source": "lixinger",
        "periods": [
            {"period_end": "2025-12-31", "currency": "CNY", "revenue": 100, "pretax_income": 25, "income_tax": 5}
        ],
        "warnings": [],
    }
    monkeypatch.setenv("LIXINGER_TOKEN", "test")
    monkeypatch.setattr(lixinger, "dcf_inputs", lambda market, code, periods: primary)
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: _yahoo_dcf_ticker())
    result = stock_data._dcf_inputs("0700.HK", 1)
    row = result["periods"][0]
    assert result["source"] == "lixinger"
    assert row["revenue"] == 100
    assert row["operating_income"] == 30
    assert row["capital_expenditure"] == 12
    assert row["effective_tax_rate"] == 0.2
    assert row["field_sources"]["revenue"] == "lixinger"
    assert row["field_sources"]["operating_income"] == "yfinance"
    assert "cash" in row["missing_fields"]
    assert row["lease_liabilities"] is None and "lease_liabilities" in row["missing_fields"]


def test_dcf_inputs_do_not_fill_from_other_currency(monkeypatch):
    import sec_edgar

    from tools import stock_data

    primary = {
        "source": "sec_companyfacts",
        "periods": [{"period_end": "2025-12-28", "currency": "USD"}],
        "warnings": [],
    }
    monkeypatch.setattr(sec_edgar, "dcf_inputs", lambda symbol, periods: primary)
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: _yahoo_dcf_ticker("EUR"))
    row = stock_data._dcf_inputs("AAPL", 1)["periods"][0]
    assert row["operating_income"] is None
    assert row["field_sources"] == {}


def test_lixinger_hk_dcf_inputs_sum_debt_and_keep_leases_separate(monkeypatch):
    import lixinger

    def metric(value):
        return {"t": value}

    rows = [
        {"date": "2025-06-30T00:00:00+08:00", "reportType": "interim_report", "stockCode": "00700"},
        {
            "date": "2025-12-31T00:00:00+08:00",
            "reportType": "annual_report",
            "stockCode": "00700",
            "currency": "CNY",
            "q": {
                "ps": {"toi": metric(100), "tp": metric(25), "ite": metric(5)},
                "bs": {"stl": metric(10), "ltl": metric(40), "bp": metric(5), "stll": metric(2), "ltll": metric(6)},
            },
        },
    ]
    monkeypatch.setattr(lixinger, "company_data", lambda market, code, dataset, metrics: rows)
    result = lixinger.dcf_inputs("hk", "00700", 2)
    assert result["periods"] == [
        {
            "period_end": "2025-12-31",
            "currency": "CNY",
            "revenue": 100,
            "pretax_income": 25,
            "income_tax": 5,
            "operating_cash_flow": None,
            "cash": None,
            "total_debt": 55,
            "lease_liabilities": 8,
            "minority_interest": None,
            "shares_outstanding": None,
        }
    ]


def test_us_sector_rankings_use_etf_proxies(monkeypatch):
    from argparse import Namespace

    import pandas as pd

    from tools import stock_data

    index = pd.to_datetime(["2026-11-12", "2026-11-13"])
    closes = pd.DataFrame({"XLK": [100, 98], "XLE": [50, 51]}, index=index)
    monkeypatch.setattr("yfinance.download", lambda *a, **k: {"Close": closes})
    result = stock_data.cmd_sector_rankings(Namespace(top=5, direction="top", board_type="industry", market="US"))
    assert [(row["etf"], row["change_pct"]) for row in result] == [("XLE", 2.0), ("XLK", -2.0)]
    assert result[0]["proxy"] == "SPDR Select Sector ETF"
    concept = stock_data.cmd_sector_rankings(Namespace(top=5, direction="top", board_type="concept", market="US"))
    assert "error" in concept


def test_quotes_batch_keeps_input_order_and_isolates_failures(monkeypatch):
    from argparse import Namespace

    from tools import stock_data

    def quote(args):
        if args.symbol == "BAD":
            raise RuntimeError("provider down")
        return {"symbol": args.symbol, "price": 1}

    monkeypatch.setattr(stock_data, "cmd_quote", quote)
    result = stock_data.cmd_quotes(Namespace(symbols="AAPL, BAD,sh600519,000001,"))
    assert [row.get("symbol") for row in result] == ["AAPL", None, "sh600519", "000001"]
    assert result[1] == {"error": "provider down"}


def test_klines_batch_passes_count_and_keeps_errors_per_row(monkeypatch):
    from argparse import Namespace

    from tools import stock_data

    seen = []

    def kline(args):
        seen.append((args.symbol, args.period, args.count))
        return {"error": "no data"} if args.symbol == "BAD" else [{"close": 1}]

    monkeypatch.setattr(stock_data, "cmd_kline", kline)
    result = stock_data.cmd_klines(Namespace(symbols="sh000001,BAD", count=30))
    assert result == [[{"close": 1}], {"error": "no data"}]
    assert sorted(seen) == [("BAD", "daily", 30), ("sh000001", "daily", 30)]


def test_baostock_sessions_do_not_overlap_across_threads(monkeypatch):
    import contextlib
    import sys
    import threading
    import time
    import types

    from tools import stock_data

    active, overlaps = [], []

    class Result:
        error_code, fields = "0", ["date", "close"]

        def next(self):
            return False

    def login():
        overlaps.append(bool(active))
        active.append(1)
        time.sleep(0.05)

    def logout():
        active.pop()

    fake = types.SimpleNamespace(login=login, logout=logout, query_history_k_data_plus=lambda *a, **k: Result())
    monkeypatch.setitem(sys.modules, "baostock", fake)

    def fetch():
        with contextlib.suppress(ValueError):  # the fake session returns no rows
            stock_data._kline_baostock("600519", "daily", 5)

    threads = [threading.Thread(target=fetch) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlaps == [False, False, False]


def test_quotes_batch_rejects_oversized_input():
    from argparse import Namespace

    from tools import stock_data

    assert "error" in stock_data.cmd_quotes(Namespace(symbols=",".join(f"S{i}" for i in range(41))))
    assert "error" in stock_data.cmd_quotes(Namespace(symbols=" , "))


def test_quotes_batch_times_out_only_the_slow_row(monkeypatch):
    import time
    from argparse import Namespace

    from tools import stock_data

    def quote(args):
        if args.symbol == "SLOW":
            time.sleep(1)
        return {"symbol": args.symbol, "price": 1}

    monkeypatch.setattr(stock_data, "cmd_quote", quote)
    monkeypatch.setattr(stock_data, "QUOTES_DEADLINE", 0.2)
    started = time.monotonic()
    result = stock_data.cmd_quotes(Namespace(symbols="AAPL,SLOW,MSFT"))
    assert time.monotonic() - started < 0.9
    assert result == [{"symbol": "AAPL", "price": 1}, {"error": "quote timed out"}, {"symbol": "MSFT", "price": 1}]


def test_quotes_cli_exits_cleanly_while_a_timed_out_thread_still_writes_stderr():
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = """
import sys, time
from tools import stock_data

def quote(args):
    if args.symbol == "SLOW":
        while True:
            sys.stderr.write("provider noise\\\\n")
    return {"symbol": args.symbol, "price": 1}

stock_data.cmd_quote = quote
stock_data.QUOTES_DEADLINE = 0.2
sys.argv = ["stock_data.py", "quotes", "AAPL,SLOW"]
stock_data.main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr[-300:]
    assert json.loads(result.stdout) == [{"symbol": "AAPL", "price": 1}, {"error": "quote timed out"}]
