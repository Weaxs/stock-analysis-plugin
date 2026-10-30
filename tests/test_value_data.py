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

    def offline(*args, **kwargs):
        raise OSError("test did not configure this HTTP source")

    monkeypatch.setattr("requests.get", offline)
    monkeypatch.setattr("requests.post", offline)
    monkeypatch.delenv("LIXINGER_TOKEN", raising=False)


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


def test_etf_details_cli_reads_disclosed_equities_with_current_akshare(monkeypatch, capsys):
    import json
    import sys
    from unittest.mock import MagicMock

    import akshare as ak

    from tools import value_data

    monkeypatch.setattr(value_data, "cached", lambda _key, _ttl, fetch: fetch())
    monkeypatch.setattr(
        ak,
        "fund_etf_fund_info_em",
        lambda **kwargs: pd.DataFrame({"净值日期": ["2026-10-09"], "单位净值": [2], "日增长率": [1]}),
    )
    monkeypatch.setattr(
        ak,
        "fund_portfolio_hold_em",
        lambda **kwargs: pd.DataFrame(
            {"股票代码": ["600519"], "股票名称": ["A"], "占净值比例": [5], "季度": ["2026年2季度股票投资明细"]}
        ),
    )
    monkeypatch.setattr("_subproc.run_tool", lambda *args, **kwargs: {"price": 2, "as_of": "2026-10-09"})
    table = (
        '<div class="boxitem"><h4><a href="/jjcc/510300.html">2026-06-30</a></h4>'
        "<table><tr><th>股票代码</th><th>股票名称</th><th>占净值比例</th><th>持股数（万股）</th>"
        "<th>持仓市值（万元）</th></tr><tr><td>600519</td><td>贵州茅台</td><td>5%</td><td>10</td><td>20</td></tr>"
        "</table></div>"
    )

    def get(url, **kwargs):
        if "FundArchivesDatas" in url:
            return MagicMock(text=f"var apidata={{content:{table!r}}};")
        if "pingzhongdata" in url:
            return MagicMock(text='var Data_fluctuationScale = {"categories":[],"series":[]};')
        raise OSError("offline")

    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(sys, "argv", ["value_data.py", "etf_details", "510300", "--include-full-holdings"])

    value_data.main()

    result = json.loads(capsys.readouterr().out)
    position = result["disclosed_equity_report"]["equity_positions"][0]
    assert position["code"] == "600519"
    assert position["weight_pct"] == 5


def test_csi_fallback_never_labels_total_share_pe_as_ttm(monkeypatch):
    import akshare as ak

    from tools import value_data

    def unavailable(**_):
        raise ValueError("no history")

    monkeypatch.setattr(ak, "stock_index_pe_lg", unavailable)
    monkeypatch.setattr(ak, "stock_index_pb_lg", unavailable)
    monkeypatch.setattr(ak, "stock_zh_index_hist_csindex", unavailable)
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


def test_csi_historical_pe_extends_recent_indicator_without_changing_basis(monkeypatch):
    import akshare as ak

    from tools import value_data

    def unsupported(**kwargs):
        raise KeyError("unsupported index")

    monkeypatch.setattr(ak, "stock_index_pe_lg", unsupported)
    monkeypatch.setattr(ak, "stock_index_pb_lg", unsupported)
    received = []

    def history(**kwargs):
        received.append(kwargs)
        return pd.DataFrame(
            {"日期": ["2020-01-01", "2022-01-01", "2026-10-09", "2026-10-12"], "滚动市盈率": [1, 10, 20, None]}
        )

    monkeypatch.setattr(ak, "stock_zh_index_hist_csindex", history)
    monkeypatch.setattr(
        ak,
        "stock_zh_index_value_csindex",
        lambda **kwargs: pd.DataFrame({"日期": ["2026-10-09"], "市盈率1": [18], "股息率1": [2]}),
    )
    result = value_data.index_valuation(Namespace(symbol="000985", years=5))
    assert result["metrics"]["pe_ttm"]["samples"] == 2
    assert result["metrics"]["pe_ttm"]["current"] == 20
    assert result["metrics"]["pe_ttm"]["as_of"] == "2026-10-09"
    assert result["metrics"]["pe"]["current"] == 18
    assert result["history"][0]["date"] == "2022-01-01"
    assert received[0]["start_date"] == "20211012"
    assert result["metrics"]["pb"]["current"] is None


def test_sse_pcf_preserves_complete_basket_cash_flags_and_original_codes(monkeypatch):
    from unittest.mock import Mock

    from tools import value_data

    xml = b"""<SSEPortfolioCompositionFile><FundInstrumentID>510300</FundInstrumentID><TradingDay>20261012</TradingDay><PreTradingDay>20261009</PreTradingDay><CreationRedemptionUnit>900000</CreationRedemptionUnit><RecordNumber>2</RecordNumber><ComponentList><Component><InstrumentID>000001</InstrumentID><InstrumentName>Bank</InstrumentName><Quantity>1600</Quantity><SubstitutionFlag>1</SubstitutionFlag><UnderlyingSecurityID>102</UnderlyingSecurityID></Component><Component><InstrumentID>00700</InstrumentID><InstrumentName>Tencent</InstrumentName><Quantity>0</Quantity><SubstitutionFlag>7</SubstitutionFlag><SubstitutionCashAmount>1234.5</SubstitutionCashAmount><UnderlyingSecurityID>103</UnderlyingSecurityID></Component></ComponentList></SSEPortfolioCompositionFile>"""
    monkeypatch.setattr("requests.get", lambda *a, **k: Mock(content=xml))
    result = value_data.etf_basket("510300")
    assert result["complete"] is True
    assert result["as_of"] == "2026-10-12"
    assert result["record_count"] == 2
    assert result["components"][1]["code"] == "00700"
    assert result["components"][1]["cash_substitution_flag"] == "7"
    assert result["components"][1]["quantity_per_unit"] == 0
    assert result["components"][1]["substitution_cash_amount"] == 1234.5


@pytest.mark.parametrize("replacement", [b"<RecordNumber>3</RecordNumber>", b"<RecordNumber>oops</RecordNumber>"])
def test_incomplete_pcf_is_not_reported_as_complete(monkeypatch, replacement):
    from unittest.mock import Mock

    from tools import value_data

    xml = b"<SSEPortfolioCompositionFile><FundInstrumentID>510300</FundInstrumentID><TradingDay>20261012</TradingDay><CreationRedemptionUnit>900000</CreationRedemptionUnit><RecordNumber>0</RecordNumber><ComponentList/></SSEPortfolioCompositionFile>"
    xml = xml.replace(b"<RecordNumber>0</RecordNumber>", replacement)
    monkeypatch.setattr("requests.get", lambda *a, **k: Mock(content=xml))
    with pytest.raises(ValueError):
        value_data.etf_basket("510300")


def test_official_northbound_shares_use_actual_published_quarter(monkeypatch):
    from unittest.mock import Mock

    from tools import value_data

    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs["params"])
        return Mock(
            json=lambda: {
                "result": [{"secCode": "600519", "tradeDate": "20260630", "totalHoldings": "53,711,656.00"}],
                "pageHelp": {"total": 1, "pageCount": 1},
            }
        )

    monkeypatch.setattr("requests.get", get)
    result = value_data.northbound_holdings("600519")
    assert result["as_of"] == "2026-06-30"
    assert result["shares"] == 53711656
    assert result["complete"] is True
    assert calls[0]["tradeDate"] == ""


def test_szse_northbound_uses_quarter_query_and_stock_units(monkeypatch):
    from unittest.mock import Mock

    from tools import value_data

    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs["params"])
        return Mock(
            json=lambda: [
                {"metadata": {"recordcount": 1, "pagecount": 1}, "data": [{"zqdm": "000001  ", "cysl": "736,878,410"}]}
            ]
        )

    monkeypatch.setattr("requests.get", get)
    result = value_data.northbound_holdings("000001", "2026-06-30")
    assert result["shares"] == 736878410
    assert result["as_of"] == "2026-06-30"
    assert calls[0]["txtDate"] == "2026Q2"
    assert calls[0]["txtZqdm"] == "000001"


def test_unpublished_northbound_quarter_is_unknown_not_zero(monkeypatch):
    from unittest.mock import Mock

    from tools import value_data

    monkeypatch.setattr(
        "requests.get", lambda *a, **k: Mock(json=lambda: {"result": [], "pageHelp": {"total": 0, "pageCount": 0}})
    )
    with pytest.raises(ValueError, match="unavailable"):
        value_data.northbound_holdings("600519", "2026-09-30")


def test_szse_cross_border_pcf_uses_total_record_count(monkeypatch):
    from unittest.mock import Mock

    from tools import value_data

    xml = b"""<PCFFile xmlns="http://ts.szse.cn/Fund"><SecurityID>159941</SecurityID><TradingDay>20261012</TradingDay><CreationRedemptionUnit>500000</CreationRedemptionUnit><RecordNum>1</RecordNum><TotalRecordNum>2</TotalRecordNum><Components><Component><UnderlyingSecurityID>159900</UnderlyingSecurityID><UnderlyingSymbol>Cash</UnderlyingSymbol><UnderlyingSecurityIDSource>102</UnderlyingSecurityIDSource><ComponentShare>0</ComponentShare><SubstituteFlag>2</SubstituteFlag></Component><Component><UnderlyingSecurityID>AAPL</UnderlyingSecurityID><UnderlyingSymbol>Apple</UnderlyingSymbol><UnderlyingSecurityIDSource>9999</UnderlyingSecurityIDSource><ComponentShare>100</ComponentShare><SubstituteFlag>2</SubstituteFlag></Component></Components></PCFFile>"""
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if url.endswith("/data"):
            return Mock(json=lambda: [{"data": [{"jjdm": "pcf_159941_20261012;159941ETF20261012"}]}])
        return Mock(content=xml)

    monkeypatch.setattr("requests.get", get)
    result = value_data.etf_basket("159941")
    assert result["record_count"] == 2
    assert result["components"][1]["code"] == "AAPL"
    assert result["components"][1]["exchange_code"] == "9999"
    assert calls[-1] == "https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_159941_20261012.xml"


def test_latest_szse_northbound_skips_unpublished_quarter(monkeypatch):
    from unittest.mock import Mock

    from tools import value_data

    calls = []

    def get(url, **kwargs):
        p = kwargs["params"]
        calls.append(p.copy())
        rows = [] if p["txtDate"] == "2026Q3" else [{"zqdm": "000001", "cysl": "42"}]
        return Mock(json=lambda: [{"metadata": {"pagecount": 1}, "data": rows}])

    monkeypatch.setattr("requests.get", get)
    result = value_data.northbound_holdings("000001")
    assert result["as_of"] == "2026-06-30"
    assert result["shares"] == 42
    assert [c["txtDate"] for c in calls] == ["2026Q3", "2026Q2", "2026Q2"]


def test_csi_history_survives_recent_indicator_failure(monkeypatch):
    import akshare as ak

    from tools import value_data

    def unavailable(**kwargs):
        raise OSError("offline")

    monkeypatch.setattr(ak, "stock_index_pe_lg", unavailable)
    monkeypatch.setattr(ak, "stock_index_pb_lg", unavailable)
    monkeypatch.setattr(ak, "stock_zh_index_value_csindex", unavailable)
    monkeypatch.setattr(
        ak, "stock_zh_index_hist_csindex", lambda **k: pd.DataFrame({"日期": ["2026-10-09"], "滚动市盈率": [20]})
    )
    result = value_data.index_valuation(Namespace(symbol="000985", years=5))
    assert result["metrics"]["pe_ttm"]["current"] == 20
    assert result["warnings"]


def test_optional_lixinger_pb_does_not_overwrite_csi_pe(monkeypatch):
    from unittest.mock import Mock

    import akshare as ak

    from tools import value_data

    def unavailable(**kwargs):
        raise OSError("unsupported")

    monkeypatch.setattr(ak, "stock_index_pe_lg", unavailable)
    monkeypatch.setattr(ak, "stock_index_pb_lg", unavailable)
    monkeypatch.setattr(ak, "stock_zh_index_value_csindex", unavailable)
    monkeypatch.setattr(
        ak, "stock_zh_index_hist_csindex", lambda **k: pd.DataFrame({"日期": ["2026-10-09"], "滚动市盈率": [20]})
    )
    monkeypatch.setenv("LIXINGER_TOKEN", "test-token")
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return Mock(
            json=lambda: {
                "code": 1,
                "data": [{"date": "2026-10-09T00:00:00+08:00", "stockCode": "000985", "pe_ttm.mcw": 22, "pb.mcw": 2.3}],
            }
        )

    monkeypatch.setattr("requests.post", post)
    result = value_data.index_valuation(Namespace(symbol="000985", years=5))
    assert result["metrics"]["pb"]["current"] == 2.3
    assert result["metrics"]["pe_ttm"]["current"] == 20
    assert result["metric_sources"]["pb"] == "lixinger"
    assert calls[0]["metricsList"] == ["pe_ttm.mcw", "pb.mcw"]
    assert "test-token" not in str(result)


def test_hk_earnings_calendar_keeps_provider_schedule_unconfirmed(monkeypatch):
    import yfinance as yf

    from tools import value_data

    monkeypatch.setattr(
        yf,
        "Ticker",
        lambda code: Namespace(
            get_earnings_dates=lambda **kw: pd.DataFrame(
                index=pd.DatetimeIndex(["2026-11-17 23:00"], tz="America/New_York")
            )
        ),
    )
    result = value_data.earnings_calendar(Namespace(symbol="0700.HK", report_date="", top=50))
    assert result["market"] == "HK"
    assert result["disclosures"][0]["scheduled_date"] == "2026-11-18"
    assert result["disclosures"][0]["confirmation"] == "provider_only"
    assert result["disclosures"][0]["report_date"] is None


@pytest.mark.parametrize(
    "page_token, decoded_token", [("anonymous", "anonymous"), ("anonymous%2Bvalue", "anonymous+value")]
)
def test_hk_dividend_ranking_uses_year_column_and_preserves_unknown(monkeypatch, page_token, decoded_token):
    from unittest.mock import Mock

    from tools import value_data

    def get(url, **kwargs):
        if "getcapitalefficiencylist" not in url:
            return Mock(text='LabCI.getToken = function() { // return "old";\n return "' + page_token + '"; }')
        data = {
            "data": {
                "responsecode": "000",
                "totalCount": 3,
                "year": ["2025", "2024"],
                "datalist": [
                    {"sym": "700", "nm": "Tencent", "period": "2025 Annual", "data": ["2.1", "2.1", "1.5"]},
                    {"sym": "5", "nm": "HSBC", "period": "2025 Annual", "data": ["4.2", "4.2", "3.5"]},
                    {"sym": "1", "nm": "Missing", "period": "2025 Annual", "data": ["N/A", "", "N/A"]},
                ],
            }
        }
        assert kwargs["params"]["token"] == decoded_token
        return Mock(status_code=200, text="stockrank(" + __import__("json").dumps(data) + ");")

    monkeypatch.setattr("requests.get", get)
    result = value_data.dividends(
        Namespace(symbol="", mode="ranking", market="HK", report_date="2024-12-31", years=10, top=20)
    )
    assert [r["symbol"] for r in result["items"]] == ["00005.HK", "00700.HK"]
    assert result["items"][0]["dividend_yield"] == 3.5
    assert result["market"] == "HK"
    assert "anonymous" not in str(result)


def test_etf_details_keeps_valid_basket_when_other_sources_fail(monkeypatch):
    import akshare as ak

    from tools import value_data

    def unavailable(**kwargs):
        raise OSError("offline")

    monkeypatch.setattr(ak, "fund_etf_fund_info_em", unavailable)
    monkeypatch.setattr(ak, "fund_portfolio_hold_em", unavailable)
    monkeypatch.setattr(value_data, "fund_size_history", lambda *a: unavailable())
    monkeypatch.setattr(value_data, "etf_basket", lambda *a: {"complete": True, "components": [{"code": "600519"}]})
    monkeypatch.setattr("_subproc.run_tool", lambda *a, **k: {})
    result = value_data.etf_details(Namespace(symbol="510300", benchmark="", days=60))
    assert result["basket"]["complete"] is True
    assert result["warnings"]


def test_pcf_includes_fund_level_cash_and_previous_nav(monkeypatch):
    from unittest.mock import Mock

    from tools import value_data

    xml = b"""<SSEPortfolioCompositionFile><FundInstrumentID>510300</FundInstrumentID><TradingDay>20261012</TradingDay><PreTradingDay>20261009</PreTradingDay><CreationRedemptionUnit>900000</CreationRedemptionUnit><RecordNumber>1</RecordNumber><NAV>4.3898</NAV><NAVperCU>3950821.74</NAVperCU><PreCashComponent>106269.74</PreCashComponent><EstimatedCashComponent>103376.74</EstimatedCashComponent><MaxCashRatio>0.5</MaxCashRatio><ComponentList><Component><InstrumentID>000001</InstrumentID><Quantity>1600</Quantity></Component></ComponentList></SSEPortfolioCompositionFile>"""
    monkeypatch.setattr("requests.get", lambda *a, **k: Mock(content=xml))
    result = value_data.etf_basket("510300")
    assert result["previous_cash_component"] == 106269.74
    assert result["estimated_cash_component"] == 103376.74
    assert result["previous_nav"]["as_of"] == "2026-10-09"
    assert result["previous_nav"]["nav_per_unit"] == 4.3898
    assert result["cash_unit"] == "CNY per creation/redemption unit"


def test_missing_nominee_quantity_is_not_zero_when_official_source_fails(monkeypatch):
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
        lambda **k: pd.DataFrame({"股东名称": ["香港中央结算有限公司"], "持股数": [None]}),
    )
    result = value_data.shareholder_structure(Namespace(symbol="600519", report_date="2026-06-30"))
    assert result["northbound_nominee"]["shares"] is None
    assert result["northbound"]["shares"] is None


def test_szse_pcf_missing_exchange_is_unknown_not_the_security_code(monkeypatch):
    from unittest.mock import Mock

    from tools import value_data

    xml = b"""<PCFFile xmlns="http://ts.szse.cn/Fund"><SecurityID>159941</SecurityID><TradingDay>20261012</TradingDay><CreationRedemptionUnit>500000</CreationRedemptionUnit><TotalRecordNum>1</TotalRecordNum><Components><Component><UnderlyingSecurityID>AAPL</UnderlyingSecurityID><UnderlyingSymbol>Apple</UnderlyingSymbol><ComponentShare>100</ComponentShare></Component></Components></PCFFile>"""

    def get(url, **kwargs):
        if url.endswith("/data"):
            return Mock(json=lambda: [{"data": [{"jjdm": "pcf_159941_20261012;159941ETF20261012"}]}])
        return Mock(content=xml)

    monkeypatch.setattr("requests.get", get)
    result = value_data.etf_basket("159941")
    assert result["components"][0]["code"] == "AAPL"
    assert result["components"][0]["exchange_code"] is None


def test_hk_calendar_prefers_official_board_notice_and_parses_day_first(monkeypatch):
    from unittest.mock import Mock

    import yfinance as yf

    from tools import value_data

    html = """<table><tr><th>BM Date</th><th></th><th>Stock Short Name</th><th>Code</th><th>Purpose</th><th>Period</th></tr><tr><td>06/11/2026</td><td></td><td>HKEX</td><td>388</td><td>RESULTS</td><td>9-MTH-ENDED30/09/26</td></tr><tr><td>07/11/2026</td><td></td><td>HKEX</td><td>388</td><td>DIVIDEND</td><td></td></tr></table>"""
    monkeypatch.setattr("requests.get", lambda *a, **k: Mock(text=html))
    monkeypatch.setattr(yf, "Ticker", lambda *a: pytest.fail("official schedule must not require Yahoo"))
    result = value_data.earnings_calendar(Namespace(symbol="0388.HK", report_date="2026-09-30", top=50))
    event = result["disclosures"][0]
    assert event["scheduled_date"] == "2026-11-06"
    assert event["report_date"] == "2026-09-30"
    assert event["confirmation"] == "official_board_meeting_notice"
    assert event["date_kind"] == "scheduled_board_meeting"
    assert event["actual_date"] is None
    assert len(result["disclosures"]) == 1


def test_hk_unknown_provider_period_is_not_assigned_to_requested_report(monkeypatch):
    import yfinance as yf

    from tools import value_data

    monkeypatch.setattr(
        yf,
        "Ticker",
        lambda *a: Namespace(
            get_earnings_dates=lambda **kw: pd.DataFrame(index=pd.DatetimeIndex(["2026-11-18"], tz="Asia/Hong_Kong"))
        ),
    )
    result = value_data.earnings_calendar(Namespace(symbol="0700.HK", report_date="2026-06-30", top=50))
    assert result["disclosures"] == []
    assert result["unverified_schedules"][0]["scheduled_date"] == "2026-11-18"
    assert result["unverified_schedules"][0]["report_date"] is None


def test_disclosed_equity_report_keeps_actual_positions_units_and_period(monkeypatch):
    import json
    from unittest.mock import Mock

    from tools import value_data

    html = """<div class="boxitem"><h4 class="t"><a href="http://fund.eastmoney.com/510300.html">Fund</a>2026年2季度股票投资明细 截止至：2026-06-30</h4><table><tr><th>序号</th><th>股票代码</th><th>股票名称</th><th>占净值比例</th><th>持股数（万股）</th><th>持仓市值（万元）</th></tr><tr><td>1</td><td>000001</td><td>Bank</td><td>1.25%</td><td>12.5</td><td>100</td></tr><tr><td>2</td><td>00700</td><td>Tencent</td><td>2.5%</td><td>3</td><td>200</td></tr></table></div>"""
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs["params"])
        return Mock(text="var apidata={content:" + json.dumps(html) + ",curyear:2026};")

    monkeypatch.setattr("requests.get", get)
    result = value_data.fund_disclosed_equities("510300")
    assert result["as_of"] == "2026-06-30"
    assert result["position_count"] == 2
    assert result["equity_positions"][0]["quantity_shares"] == 125000
    assert result["equity_positions"][0]["market_value_cny"] == 1000000
    assert result["equity_positions"][1]["code"] == "00700"
    assert result["is_realtime"] is False
    assert calls[0]["year"] == "2026" and calls[0]["month"] == "6"
    assert calls[0]["topline"] == 10000


def test_full_disclosed_holdings_is_explicit_opt_in(monkeypatch):
    from tools import value_data

    called = []

    def report(symbol):
        called.append(symbol)
        return {"as_of": "2026-06-30", "position_count": 339}

    monkeypatch.setattr(value_data, "fund_disclosed_equities", report)
    monkeypatch.setattr(value_data, "dataset", lambda *a, **k: [])
    monkeypatch.setattr(value_data, "fund_size_history", lambda *a: [])
    monkeypatch.setattr(value_data, "etf_basket", lambda *a: {"complete": True})
    monkeypatch.setattr("_subproc.run_tool", lambda *a, **k: {})
    args = Namespace(symbol="510300", benchmark="", days=60, include_full_holdings=False)
    assert "disclosed_equity_report" not in value_data.etf_details(args)
    assert called == []
    args.include_full_holdings = True
    assert value_data.etf_details(args)["disclosed_equity_report"]["position_count"] == 339


def test_disclosed_equities_optional_name_does_not_break_json(monkeypatch):
    import json
    from unittest.mock import Mock

    from tools import value_data

    html = """<div class="boxitem"><h4><a href="http://fund.eastmoney.com/510300.html">Fund</a>2026-06-30</h4><table><tr><th>股票代码</th><th>股票名称</th><th>占净值比例</th><th>持股数（万股）</th><th>持仓市值（万元）</th></tr><tr><td>000001</td><td></td><td>1%</td><td>10</td><td>100</td></tr></table></div>"""
    monkeypatch.setattr("requests.get", lambda *a, **k: Mock(text="var apidata={content:" + json.dumps(html) + "};"))
    result = value_data.fund_disclosed_equities("510300")
    assert result["equity_positions"][0]["name"] is None
    json.dumps(result, allow_nan=False)
