from argparse import Namespace
from types import SimpleNamespace

import pytest

from tools import stock_data, value_data


def test_hk_valuation_uses_lixinger_daily_history(monkeypatch):
    monkeypatch.setenv("LIXINGER_TOKEN", "test-token")
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "code": 1,
                "data": [
                    {"date": "2026-01-01", "pe_ttm": 10, "pb": 1},
                    {"date": "2026-01-02", "pe_ttm": 20, "pb": 2},
                    {"date": "2026-01-03", "pe_ttm": 15, "pb": 1.5},
                ],
            },
        )

    monkeypatch.setattr("requests.post", post)
    result = value_data.valuation_history(Namespace(symbol="0700.HK", years=5))
    assert result["source"] == "lixinger"
    assert result["metrics"]["pe_ttm"]["percentile"] == pytest.approx(66.6667, abs=0.0001)
    assert result["metrics"]["pb"]["bands"]["50"] == 1.5
    assert calls[0][1]["stockCodes"] == ["00700"]
    assert calls[0][1]["metricsList"] == ["pe_ttm", "pb"]


def test_hk_annual_financials_keep_non_december_fiscal_year_and_units(monkeypatch):
    monkeypatch.setenv("LIXINGER_TOKEN", "test-token")

    def post(url, **kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "code": 1,
                "data": [
                    {
                        "date": "2025-03-31",
                        "reportDate": "2025-06-12",
                        "reportType": "annual_report",
                        "currency": "HKD",
                        "q": {
                            "m": {"roe": {"t": 0.12}},
                            "ps": {"gp_m": {"t": 0.4}, "np_s_r": {"t": 0.2}, "toi": {"t": 100}, "np": {"t": 20}},
                        },
                    },
                    {"date": "2025-09-30", "reportType": "interim_report", "q": {"m": {"roe": {"t": 0.06}}}},
                ],
            },
        )

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(
        stock_data,
        "financials_yf",
        lambda *a: {"symbol": "0700.HK", "name": "Tencent", "error": "Financial data unavailable"},
    )
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert result["source"] == "lixinger"
    assert all(
        key in result for key in ("name", "market_cap", "pe", "forward_pe", "pb", "debt_to_equity", "dividend_yield")
    )
    assert result["roe"] == pytest.approx(0.12)  # existing Yahoo snapshot uses fraction
    assert result["periods"][0]["roe"] == 12
    assert result["periods"][0]["gross_margin"] == 40
    assert result["periods"][0]["report_date"] == "2025-03-31"
    assert result["periods"][0]["disclosure_date"] == "2025-06-12"
    assert result["periods"][0]["currency"] == "HKD"
    assert result["available_periods"] == 1
    assert result["warnings"]


def test_hk_financials_permission_failure_retains_existing_data(monkeypatch):
    monkeypatch.setenv("LIXINGER_TOKEN", "do-not-leak-this-token")
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"code": 0, "message": "do-not-leak-this-token"}
        ),
    )
    monkeypatch.setattr(
        stock_data,
        "financials_yf",
        lambda *a: {
            "symbol": "0700.HK",
            "name": "Tencent",
            "periods": [{"period_end": "2025-12-31", "net_income": 20}],
        },
    )
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert result["name"] == "Tencent"
    assert result["periods"][0]["net_income"] == 20
    assert len(result["warnings"]) == 2
    assert "do-not-leak-this-token" not in str(result)


def test_hk_valuation_permission_failure_uses_baidu_without_mixing(monkeypatch):
    import akshare as ak
    import pandas as pd

    monkeypatch.setenv("LIXINGER_TOKEN", "test-token")
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"code": 0})
    )
    monkeypatch.setattr(
        ak, "stock_hk_valuation_baidu", lambda **k: pd.DataFrame({"date": ["2026-01-01"], "value": [10]})
    )
    result = value_data.valuation_history(Namespace(symbol="0700.HK", years=5))
    assert result["source"] == "baidu"
    assert result["history"] == [{"date": "2026-01-01", "pe_ttm": 10.0, "pb": 10.0}]
    assert result["warnings"]


def test_lixinger_request_does_not_exceed_ten_year_window(monkeypatch):
    from datetime import date

    import lixinger

    class MonthEnd(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 31)

    monkeypatch.setattr(lixinger, "date", MonthEnd)
    monkeypatch.setenv("LIXINGER_TOKEN", "test-token")
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"code": 1, "data": [{"date": "2026-10-30", "pb": 2}]}
        )

    monkeypatch.setattr("requests.post", post)
    lixinger.hk_data("0700.HK", "fundamental", ["pb"])
    assert calls[0]["startDate"] == "2016-10-31"
    assert calls[0]["endDate"] == "2026-10-31"


def test_hk_enrichment_preserves_existing_snapshot(monkeypatch):
    monkeypatch.setenv("LIXINGER_TOKEN", "test-token")
    import lixinger

    monkeypatch.setattr(
        lixinger,
        "financials_hk",
        lambda *a: {
            "source": "lixinger",
            "periods": [{"roe": 12}],
            "available_periods": 1,
            "roe": 0.12,
            "market_cap": None,
            "warnings": ["short history"],
        },
    )
    monkeypatch.setattr(
        stock_data,
        "financials_yf",
        lambda *a: {"name": "Tencent", "pe": 20, "market_cap": 1000, "roe": 0.22, "symbol": "0700.HK"},
    )
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert result["name"] == "Tencent"
    assert result["market_cap"] == 1000 and result["pe"] == 20
    assert result["roe"] == 0.22
    assert result["periods"] == [{"roe": 12}]
    assert result["warnings"] == ["short history"]
