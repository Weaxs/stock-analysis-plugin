from argparse import Namespace
from types import SimpleNamespace

import pandas as pd
import pytest

from tools import stock_data, value_data


def reply(data, code=1):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"code": code, "data": data})


def test_a_share_valuation_uses_lixinger_company_history(monkeypatch):
    monkeypatch.setenv("LIXINGER_TOKEN", "test-token")
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return reply(
            [
                {"date": "2026-01-01T00:00:00+08:00", "pe_ttm": 10, "pb": 1},
                {"date": "2026-01-02T00:00:00+08:00", "pe_ttm": 20, "pb": 2},
            ]
        )

    monkeypatch.setattr("requests.post", post)
    result = value_data.valuation_history(Namespace(symbol="sh600519", years=5))
    assert result["source"] == "lixinger"
    assert result["history"][-1] == {"date": "2026-01-02", "pe_ttm": 20.0, "pb": 2.0}
    assert calls[0][0] == "https://open.lixinger.com/api/cn/company/fundamental/non_financial"
    assert calls[0][1]["stockCodes"] == ["600519"]
    assert "warnings" not in result


def test_a_share_valuation_falls_back_to_baidu_without_mixing(monkeypatch):
    import akshare as ak

    monkeypatch.setenv("LIXINGER_TOKEN", "do-not-leak")
    monkeypatch.setattr("requests.post", lambda *a, **k: reply(None, code=0))
    monkeypatch.setattr(
        ak, "stock_zh_valuation_baidu", lambda **k: pd.DataFrame({"date": ["2026-01-01"], "value": [12]})
    )
    result = value_data.valuation_history(Namespace(symbol="600519", years=5))
    assert result["source"] == "baidu"
    assert any("Lixinger A" in warning for warning in result["warnings"])
    assert "do-not-leak" not in str(result)


def test_lixinger_cn_dcf_inputs_map_annual_statement_fields(monkeypatch):
    import lixinger

    def metric(value):
        return {"t": value}

    rows = [
        {
            "date": "2025-12-31T00:00:00+08:00",
            "reportType": "annual_report",
            "stockCode": "600519",
            "currency": "CNY",
            "q": {
                "ps": {"toi": metric(100), "op": metric(30), "tp": metric(29), "ite": metric(7)},
                "cfs": {"ncffoa": metric(40), "cpfpfiaolta": metric(12), "daa": metric(6), "bocaceatpe": metric(50)},
                "bs": {"stl": metric(5), "ncldwioy": metric(3), "bp": metric(2), "ll": metric(4), "tsc": metric(9)},
            },
        },
        {"date": "2025-09-30T00:00:00+08:00", "reportType": "third_quarterly_report", "stockCode": "600519"},
    ]
    seen = []
    monkeypatch.setattr(
        lixinger, "company_data", lambda market, code, dataset, metrics: seen.append((market, code, dataset)) or rows
    )
    result = lixinger.dcf_inputs("cn", "600519", 3)
    assert seen == [("cn", "600519", "fs")]
    assert len(result["periods"]) == 1
    row = result["periods"][0]
    assert (row["operating_income"], row["capital_expenditure"], row["depreciation_amortization"]) == (30, 12, 6)
    assert (row["cash"], row["total_debt"], row["lease_liabilities"], row["shares_outstanding"]) == (50, 10, 4, 9)
    assert row["minority_interest"] is None


def test_a_share_dcf_inputs_prefer_lixinger_and_fill_from_yahoo_shanghai_ticker(monkeypatch):
    import lixinger

    monkeypatch.setenv("LIXINGER_TOKEN", "test-token")
    primary = {
        "source": "lixinger",
        "periods": [{"period_end": "2025-12-31", "currency": "CNY", "revenue": 100}],
        "warnings": [],
    }
    calls, tickers = [], []
    monkeypatch.setattr(lixinger, "dcf_inputs", lambda market, code, periods: calls.append((market, code)) or primary)
    ticker = SimpleNamespace(
        info={"shortName": "Moutai", "financialCurrency": "CNY"},
        income_stmt=pd.DataFrame({"2025-12-31": [8]}, index=["Diluted Average Shares"]),
        cashflow=pd.DataFrame(),
        balance_sheet=pd.DataFrame(),
    )
    monkeypatch.setattr("yfinance.Ticker", lambda symbol: tickers.append(symbol) or ticker)
    result = stock_data._dcf_inputs("600519", 1)
    row = result["periods"][0]
    assert calls == [("cn", "600519")]
    assert tickers == ["600519.SS"]
    assert result["source"] == "lixinger"
    assert row["diluted_shares"] == 8
    assert row["field_sources"] == {"revenue": "lixinger", "diluted_shares": "yfinance"}


def test_beijing_dcf_inputs_without_lixinger_report_unavailable(monkeypatch):
    monkeypatch.delenv("LIXINGER_TOKEN", raising=False)
    result = stock_data._dcf_inputs("920001", 1)
    assert "error" in result
    assert "Beijing" in result["error"]


@pytest.mark.parametrize("symbol", ["7203.T"])
def test_dcf_inputs_reject_unsupported_markets(symbol):
    assert "A, HK and US" in stock_data._dcf_inputs(symbol, 1)["error"]
