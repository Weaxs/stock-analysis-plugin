from argparse import Namespace
from datetime import date

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    from tools import value_data

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 12)

    monkeypatch.setattr(value_data, "date", FixedDate)


def test_stock_valuation_percentiles_ignore_losses_and_sort_dates(monkeypatch):
    import akshare as ak

    from tools import value_data

    df = pd.DataFrame({"date": ["2024-01-03", "2024-01-01", "2024-01-02", "2024-01-04"], "value": [30, 10, -5, 20]})
    monkeypatch.setattr(ak, "stock_zh_valuation_baidu", lambda **k: df)
    result = value_data.valuation_history(Namespace(symbol="600519", years=5))
    pe = result["metrics"]["pe_ttm"]
    assert pe["current"] == 20
    assert pe["percentile"] == pytest.approx(66.6667, abs=0.0001)
    assert pe["samples"] == 3
    assert pe["bands"]["50"] == 20
    assert pe["as_of"] == "2024-01-04"
    assert len(result["history"]) == 4


def test_earnings_calendar_prefers_latest_revision_and_keeps_actual(monkeypatch):
    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        ak,
        "stock_report_disclosure",
        lambda **k: pd.DataFrame(
            {
                "股票代码": ["600519"],
                "股票简称": ["茅台"],
                "首次预约": ["2026-10-15"],
                "初次变更": ["2026-10-20"],
                "二次变更": [None],
                "三次变更": [None],
                "实际披露": [None],
            }
        ),
    )
    monkeypatch.setattr(
        ak,
        "stock_yjyg_em",
        lambda **k: pd.DataFrame({"股票代码": ["600519"], "公告日期": ["2026-10-01"], "预告类型": ["预增"]}),
    )
    result = value_data.earnings_calendar(Namespace(symbol="600519", report_date="2026-09-30", top=50))
    assert result["disclosures"][0]["scheduled_date"] == "2026-10-20"
    assert result["disclosures"][0]["original_scheduled_date"] == "2026-10-15"
    assert result["disclosures"][0]["scheduled_date_revisions"] == [{"revision": 1, "scheduled_date": "2026-10-20"}]
    assert result["disclosures"][0]["actual_date"] is None
    assert result["forecasts"][0]["announcement_date"] == "2026-10-01"


def test_hk_southbound_is_dated_and_normalized(monkeypatch):
    from tools import value_data

    html = """<input name="txtShareholdingDate" value="2026/10/09"><table id="mutualmarket-result"><thead><tr><th>Stock Code</th><th>Name</th><th>Shareholding in CCASS</th><th>% of the total number of Issued Shares/Units</th></tr></thead><tbody><tr><td>Stock Code: 700</td><td>Name: TENCENT</td><td>Shareholding in CCASS: 1,000,000</td><td>% of the total number of Issued Shares/Units: 12.5%</td></tr></tbody></table>"""
    from unittest.mock import MagicMock

    monkeypatch.setattr("requests.get", lambda *a, **k: MagicMock(text=html))
    result = value_data.shareholder_structure(Namespace(symbol="0700.HK", report_date=""))
    assert result["southbound"]["as_of"] == "2026-10-09"
    assert result["southbound"]["shares"] == 1000000
    assert result["southbound"]["holding_pct"] == 12.5


def test_a_shareholders_keep_disclosure_dates_and_report_changes(monkeypatch):
    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        ak,
        "stock_zh_a_gdhs_detail_em",
        lambda **k: pd.DataFrame(
            {
                "股东户数统计截止日": ["2026-06-30", "2026-03-31"],
                "股东户数-本次": [100, 110],
                "股东户数-增减比例": [-9.09, 10],
            }
        ),
    )
    monkeypatch.setattr(
        ak,
        "stock_gdfx_free_top_10_em",
        lambda **k: pd.DataFrame(
            {"股东名称": ["香港中央结算有限公司"], "持股数": [1000], "占总流通股本持股比例": [1.2], "增减": ["新进"]}
        ),
    )
    result = value_data.shareholder_structure(Namespace(symbol="600519", report_date="2026-06-30"))
    assert result["top_float_shareholders"][0]["shares"] == 1000
    assert result["northbound"]["as_of"] == "2026-06-30"
    assert result["shareholder_counts"][0]["holders"] == 100


def test_dividends_ignore_proposals_and_compute_realized_yield(monkeypatch):
    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        ak,
        "stock_fhps_detail_em",
        lambda **k: pd.DataFrame(
            {
                "报告期": ["2025-12-31", "2025-06-30", "2024-12-31", "2026-06-30"],
                "除权除息日": ["2026-06-01", "2025-09-01", "2025-06-01", "2026-12-01"],
                "现金分红-现金分红比例": [10, 5, 10, 20],
                "方案进度": ["实施分配", "实施分配", "实施分配", "董事会预案"],
            }
        ),
    )
    monkeypatch.setattr("_subproc.run_tool", lambda *a, **k: {"price": 20, "as_of": "2026-10-12"})
    result = value_data.dividends(Namespace(symbol="600519", mode="history", report_date="", top=20, years=10))
    assert result["cash_per_share_ttm"] == 1
    assert result["dividend_yield_ttm"] == 5
    assert result["consecutive_payment_years"] == 2
    assert len(result["calendar"]) == 1


def test_dividend_ranking_is_report_period_yield(monkeypatch):
    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        ak,
        "stock_fhps_em",
        lambda **k: pd.DataFrame(
            {
                "代码": ["600519", "000001"],
                "名称": ["A", "B"],
                "现金分红-股息率": [2, 5],
                "方案进度": ["实施分配", "实施分配"],
            }
        ),
    )
    result = value_data.dividends(Namespace(symbol="", mode="ranking", report_date="2025-12-31", top=1, years=10))
    assert result["items"][0]["symbol"] == "000001"
    assert result["items"][0]["dividend_yield"] == 5
    assert result["yield_basis"] == "provider report-period cash dividend yield, percent"


def test_tracking_error_aligns_daily_returns_without_filling_missing_dates():
    from tools import value_data

    nav = [
        {"date": "2026-01-05", "daily_return_pct": 0},
        {"date": "2026-01-06", "daily_return_pct": 1},
        {"date": "2026-01-07", "daily_return_pct": 2},
        {"date": "2026-01-08", "daily_return_pct": 1},
    ]
    benchmark = [
        {"date": "2026-01-05", "close": 100},
        {"date": "2026-01-06", "close": 101},
        {"date": "2026-01-07", "close": 102.01},
        {"date": "2026-01-08", "close": 103.0301},
    ]
    result = value_data.tracking_error(nav, benchmark)
    assert result["observations"] == 3
    assert result["annualized_tracking_error_pct"] == pytest.approx(9.1652, abs=0.0001)


def test_etf_size_history_and_nav_date_comparability(monkeypatch):
    from unittest.mock import MagicMock

    import akshare as ak

    from tools import value_data

    text = 'var Data_fluctuationScale = {"categories":["2025-12-31","2026-06-30"],"series":[{"y":10},{"y":12}]};'
    monkeypatch.setattr("requests.get", lambda *a, **k: MagicMock(text=text))
    monkeypatch.setattr(
        ak,
        "fund_etf_fund_info_em",
        lambda **k: pd.DataFrame({"净值日期": ["2026-10-09"], "单位净值": [2], "累计净值": [2], "日增长率": [1]}),
    )
    monkeypatch.setattr(
        ak,
        "fund_portfolio_hold_em",
        lambda **k: pd.DataFrame(
            {"股票代码": ["600519"], "股票名称": ["A"], "占净值比例": [5], "季度": ["2026年2季度股票投资明细"]}
        ),
    )
    monkeypatch.setattr(
        "_subproc.run_tool", lambda *a, **k: {"price": 2.2, "as_of": "2026-10-12", "premium_discount_rate": 10}
    )
    result = value_data.etf_details(Namespace(symbol="510300", benchmark="", days=60))
    assert result["size_history"][-1]["aum_cny"] == 1200000000
    assert result["size_change_pct"] == 20
    assert result["premium_discount_pct"] is None
    assert result["nav_date"] == "2026-10-09"
    assert result["holdings"][0]["weight_pct"] == 5


def test_csi_fallback_never_labels_total_share_pe_as_ttm(monkeypatch):
    import akshare as ak

    from tools import value_data

    def unavailable(**_):
        raise ValueError("no history")

    monkeypatch.setattr(ak, "stock_index_pe_lg", unavailable)
    monkeypatch.setattr(ak, "stock_index_pb_lg", unavailable)
    monkeypatch.setattr(
        ak,
        "stock_zh_index_value_csindex",
        lambda **k: pd.DataFrame({"日期": ["2026-10-09"], "市盈率1": [15], "股息率1": [2]}),
    )
    result = value_data.index_valuation(Namespace(symbol="000985", years=5))
    assert result["metrics"]["pe"]["current"] == 15
    assert "pe_ttm" not in result["history"][0]
    assert result["source"] == "csindex"


@pytest.mark.parametrize(
    "argv",
    [
        ["valuation_history", "AAPL"],
        ["valuation_history", "600519", "--years", "0"],
        ["index_valuation", "000300", "--years", "11"],
        ["earnings_calendar", "--report-date", "2026-02-30"],
        ["earnings_calendar", "--report-date", "2026-10-12"],
        ["shareholder_structure", "AAPL"],
        ["etf_details", "600519"],
        ["dividends", "--mode", "history"],
        ["dividends", "--mode", "ranking", "--symbol", "600519"],
    ],
)
def test_cli_invalid_inputs_return_json_error(monkeypatch, capsys, argv):
    import json
    import sys

    from tools import value_data

    monkeypatch.setattr(sys, "argv", ["value_data.py", *argv])
    with pytest.raises(SystemExit) as exc:
        value_data.main()
    assert exc.value.code == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_current_negative_pe_has_no_percentile(monkeypatch):
    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        ak,
        "stock_zh_valuation_baidu",
        lambda **k: pd.DataFrame({"date": ["2026-10-08", "2026-10-09"], "value": [10, -5]}),
    )
    result = value_data.valuation_history(Namespace(symbol="600519", years=5))
    assert result["metrics"]["pe_ttm"]["current"] == -5
    assert result["metrics"]["pe_ttm"]["percentile"] is None


def test_tracking_error_does_not_match_different_dates():
    from tools import value_data

    result = value_data.tracking_error(
        [{"date": "2026-01-02", "daily_return_pct": 2}],
        [{"date": "2026-01-03", "close": 100}, {"date": "2026-01-04", "close": 101}],
    )
    assert result["observations"] == 0
    assert result["annualized_tracking_error_pct"] is None


def test_tracking_error_excludes_mismatched_return_intervals():
    from tools import value_data

    nav = [{"date": d, "daily_return_pct": 10} for d in ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]]
    benchmark = [
        {"date": "2026-01-05", "close": 100},
        {"date": "2026-01-07", "close": 121},
        {"date": "2026-01-08", "close": 133.1},
    ]
    result = value_data.tracking_error(nav, benchmark)
    assert result["observations"] == 1
    assert result["annualized_tracking_error_pct"] is None


def test_hk_timezone_is_preserved_when_serializing_dividend_dates():
    from tools import value_data

    frame = pd.DataFrame({"Date": [pd.Timestamp("2026-06-01", tz="Asia/Hong_Kong")], "cash_per_share": [1]})
    row = value_data.records(frame)[0]
    assert value_data.iso(row["Date"]) == "2026-06-01"


def test_h_share_nominee_is_not_reported_as_northbound(monkeypatch):
    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        ak,
        "stock_zh_a_gdhs_detail_em",
        lambda **k: pd.DataFrame({"股东户数统计截止日": ["2026-06-30"], "股东户数-本次": [100]}),
    )
    monkeypatch.setattr(
        ak,
        "stock_gdfx_free_top_10_em",
        lambda **k: pd.DataFrame({"股东名称": ["香港中央结算(代理人)有限公司"], "持股数": [1000]}),
    )
    result = value_data.shareholder_structure(Namespace(symbol="600519", report_date="2026-06-30"))
    assert result["northbound"]["shares"] is None


def test_hk_dividend_backend_uses_yahoo_four_digit_code(monkeypatch):
    from unittest.mock import MagicMock

    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        ak, "stock_hk_dividend_payout_em", lambda **k: pd.DataFrame({"除净日": ["2026-12-01"], "分红方案": ["已公告"]})
    )
    ticker = MagicMock()
    ticker.dividends = pd.Series([1.0], index=pd.DatetimeIndex(["2026-06-01"], tz="Asia/Hong_Kong", name="Date"))
    ticker.info = {"currentPrice": 20, "currency": "HKD"}
    calls = []
    monkeypatch.setattr("yfinance.Ticker", lambda s: calls.append(s) or ticker)
    result = value_data.dividends(Namespace(symbol="0700.HK", mode="history", report_date="", top=20, years=10))
    assert calls == ["0700.HK"]
    assert result["history"][0]["ex_date"] == "2026-06-01"


def test_etf_foreign_security_codes_are_not_padded_into_a_share_codes(monkeypatch):
    from unittest.mock import MagicMock

    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        "requests.get", lambda *a, **k: MagicMock(text='var Data_fluctuationScale = {"categories":[],"series":[]};')
    )
    monkeypatch.setattr(
        ak,
        "fund_etf_fund_info_em",
        lambda **k: pd.DataFrame({"净值日期": ["2026-10-09"], "单位净值": [2], "日增长率": [1]}),
    )
    monkeypatch.setattr(
        ak,
        "fund_portfolio_hold_em",
        lambda **k: pd.DataFrame(
            {"股票代码": ["00700"], "股票名称": ["腾讯控股"], "占净值比例": [5], "季度": ["2026年2季度股票投资明细"]}
        ),
    )
    monkeypatch.setattr("_subproc.run_tool", lambda *a, **k: {"price": 2, "as_of": "2026-10-09"})
    result = value_data.etf_details(Namespace(symbol="159920", benchmark="", days=60))
    assert result["holdings"][0]["code"] == "00700"


def test_hk_foreign_currency_dividends_are_not_divided_by_hkd_price(monkeypatch):
    from unittest.mock import MagicMock

    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(ak, "stock_hk_dividend_payout_em", lambda **k: pd.DataFrame({"除净日": ["2026-12-01"]}))
    ticker = MagicMock()
    ticker.dividends = pd.DataFrame(
        {"Dividends": [1.0], "currency": ["USD"]},
        index=pd.DatetimeIndex(["2026-06-01"], tz="Asia/Hong_Kong", name="Date"),
    )
    ticker.info = {"currentPrice": 100, "currency": "HKD"}
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    result = value_data.dividends(Namespace(symbol="0005.HK", mode="history", report_date="", top=20, years=10))
    assert result["cash_per_share_ttm"] is None
    assert result["dividend_yield_ttm"] is None
    assert result["cash_per_share_ttm_by_currency"] == {"USD": 1.0}
    assert result["warnings"]


def test_hk_valuation_requests_supported_provider_window(monkeypatch):
    import akshare as ak

    from tools import value_data

    periods = []

    def fetch(**kwargs):
        periods.append(kwargs["period"])
        return pd.DataFrame({"date": ["2026-10-09"], "value": [20]})

    monkeypatch.setattr(ak, "stock_hk_valuation_baidu", fetch)
    result = value_data.valuation_history(Namespace(symbol="0700.HK", years=5))
    assert periods == ["全部", "全部"]
    assert result["market"] == "HK"


@pytest.mark.parametrize(
    "function_name", ["valuation_history", "earnings_calendar", "shareholder_structure", "dividends"]
)
def test_stock_datasets_never_relabel_explicit_indices_as_stocks(function_name, monkeypatch):
    from tools import value_data

    args = Namespace(symbol="sh000001", years=5, report_date="2026-06-30", mode="history", top=20)
    import akshare as ak

    def unexpected(**_):
        raise ValueError("unexpected provider call")

    for name in (
        "stock_zh_valuation_baidu",
        "stock_zh_a_gdhs_detail_em",
        "stock_gdfx_free_top_10_em",
        "stock_fhps_detail_em",
    ):
        monkeypatch.setattr(ak, name, unexpected)
    with pytest.raises(ValueError, match="index"):
        getattr(value_data, function_name)(args)


def test_invalid_explicit_shareholder_report_date_is_not_ignored(monkeypatch):
    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(
        ak,
        "stock_zh_a_gdhs_detail_em",
        lambda **k: pd.DataFrame({"股东户数统计截止日": ["2026-06-30"], "股东户数-本次": [100]}),
    )
    monkeypatch.setattr("stock_data.cmd_financials", lambda *a, **k: {})
    with pytest.raises(ValueError, match="report_date"):
        value_data.shareholder_structure(Namespace(symbol="600519", report_date="2026-02-30"))


@pytest.mark.parametrize("failed_source", ["calendar", "cash", "quote"])
def test_hk_dividend_sources_fail_independently(monkeypatch, failed_source):
    import akshare as ak
    import yfinance as yf

    from tools import value_data

    def calendar(**kwargs):
        if failed_source == "calendar":
            raise OSError("offline")
        return pd.DataFrame({"除净日": ["2026-12-01"]})

    class Ticker:
        @property
        def dividends(self):
            if failed_source == "cash":
                raise OSError("offline")
            return pd.Series([1.0], index=pd.DatetimeIndex(["2026-06-01"], name="Date"))

        @property
        def info(self):
            if failed_source == "quote":
                raise OSError("offline")
            return {"currentPrice": 20, "currency": "HKD"}

    monkeypatch.setattr(ak, "stock_hk_dividend_payout_em", calendar)
    monkeypatch.setattr(yf, "Ticker", lambda *a: Ticker())
    result = value_data.dividends(Namespace(symbol="0700.HK", mode="history", report_date="", top=20, years=10))
    assert result["warnings"]
    assert len(result["history"]) == (0 if failed_source == "cash" else 1)
    assert len(result["calendar"]) == (0 if failed_source == "calendar" else 1)
    assert result["dividend_yield_ttm"] == (5 if failed_source == "calendar" else None)


@pytest.mark.parametrize("mode", ["history", "calendar"])
def test_hk_dividend_unavailable_sources_do_not_look_like_empty_calendar(monkeypatch, mode):
    import yfinance as yf

    from tools import value_data

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(value_data, "dataset", unavailable)
    monkeypatch.setattr(yf, "Ticker", lambda *a: Namespace(info={}))
    with pytest.raises(ValueError, match="unavailable"):
        value_data.dividends(Namespace(symbol="0700.HK", mode=mode, report_date="", top=20, years=10))


@pytest.mark.parametrize(
    "dates, amounts",
    [(["2026-06-01"], [None]), (["2026-06-01", "2026-08-01"], [1, None]), ([None, "2026-06-01"], [1, 1])],
)
def test_missing_paid_dividend_amount_or_date_does_not_understate_yield(monkeypatch, dates, amounts):
    import akshare as ak
    import yfinance as yf

    from tools import value_data

    monkeypatch.setattr(ak, "stock_hk_dividend_payout_em", lambda **k: pd.DataFrame())
    ticker = Namespace(
        dividends=pd.Series(amounts, index=pd.DatetimeIndex(dates, name="Date")),
        info={"currentPrice": 20, "currency": "HKD"},
    )
    monkeypatch.setattr(yf, "Ticker", lambda *a: ticker)
    result = value_data.dividends(Namespace(symbol="0700.HK", mode="history", report_date="", top=20, years=10))
    assert result["cash_per_share_ttm"] is None
    assert result["dividend_yield_ttm"] is None
    assert result["consecutive_payment_years"] is None
    assert result["warnings"]
