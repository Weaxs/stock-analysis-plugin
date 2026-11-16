"""Optional Lixinger open-API provider: CN/HK non-financial companies and CN indexes.

Callers pass a provider market ("cn"/"hk") and the provider's stock code; symbol parsing stays with them."""

import os
from datetime import timedelta

import pandas as pd
import requests
from _cache import DAY, cached
from trading_calendar import market_now

API = "https://open.lixinger.com/api"


def post(path, payload):
    response = requests.post(f"{API}/{path}", json={"token": os.environ["LIXINGER_TOKEN"], **payload}, timeout=20)
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 1 or not isinstance(body.get("data"), list):
        raise ValueError(f"Lixinger {path} unavailable; check token permissions/company type")
    return body["data"]


def company_data(market, code, dataset, metrics):
    end = market_now("HK" if market == "hk" else "CN").date()
    try:
        start = end.replace(year=end.year - 10)
    except ValueError:
        start = end.replace(year=end.year - 10, day=28) + timedelta(days=1)

    def fetch():
        data = post(
            f"{market}/company/{dataset}/non_financial",
            {"stockCodes": [code], "startDate": start.isoformat(), "endDate": end.isoformat(), "metricsList": metrics},
        )
        if not data:
            raise ValueError(f"Lixinger {market} non-financial data unavailable; check token permissions/company type")
        return data

    return cached(f"lixinger-{market}:{dataset}:{code}:{end}:{','.join(metrics)}", DAY, fetch)


def valuation_history(market, code, years):
    """Daily PE-TTM/PB within the last `years`; raises when either metric is entirely missing."""
    cutoff = (market_now("HK" if market == "hk" else "CN").date() - pd.DateOffset(years=years)).date().isoformat()
    history = []
    for row in company_data(market, code, "fundamental", ["pe_ttm", "pb"]):
        stamp = str(row.get("date") or "")[:10]
        if stamp >= cutoff:
            history.append({"date": stamp, **{field: number(row.get(field)) for field in ("pe_ttm", "pb")}})
    history.sort(key=lambda r: r["date"])
    if not history or not all(any(r[field] is not None for r in history) for field in ("pe_ttm", "pb")):
        raise ValueError("Lixinger valuation history incomplete")
    return history


def number(value):
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) and abs(value) != float("inf") else None


def metric(row, field):
    table, name = field.split(".")
    return row.get("q", {}).get(table, {}).get(name, {}).get("t")


def financials_hk(code, periods, annual):
    fields = {
        "roe": "m.roe",
        "gross_margin": "ps.gp_m",
        "net_profit_margin": "ps.np_s_r",
        "total_revenue": "ps.toi",
        "net_income": "ps.np",
        "operating_cash_flow": "cfs.ncffoa",
    }
    data = company_data("hk", code, "fs", [f"q.{field}.t" for field in fields.values()])
    history = []
    for row in sorted(data, key=lambda r: r.get("date", ""), reverse=True):
        if annual and row.get("reportType") != "annual_report":
            continue
        if not row.get("date") or row.get("stockCode", code) != code:
            continue
        entry = {
            "report_date": row["date"][:10],
            "disclosure_date": (row.get("reportDate") or "")[:10] or None,
            "report_type": row.get("reportType"),
            "currency": row.get("currency"),
            "source": "lixinger",
        }
        for key, field in fields.items():
            value = metric(row, field)
            entry[key] = (
                value * 100 if value is not None and key in ("roe", "gross_margin", "net_profit_margin") else value
            )
        if any(entry.get(key) is not None for key in fields):
            history.append(entry)
    if not history:
        raise ValueError("Lixinger returned no matching financial periods")
    history = history[:periods]
    latest = history[0]
    result = {
        **dict.fromkeys(("name", "market_cap", "pe", "forward_pe", "pb", "debt_to_equity", "dividend_yield")),
        "snapshot_available": False,
        "source": "lixinger",
        "report_date": latest["report_date"],
        "currency": latest["currency"],
        "total_revenue": latest["total_revenue"],
        "net_income": latest["net_income"],
        "roe": latest["roe"] / 100 if latest["roe"] is not None else None,
        "profit_margin": latest["net_profit_margin"] / 100 if latest["net_profit_margin"] is not None else None,
        "periods": history,
        "requested_periods": periods,
        "available_periods": len(history),
        "history_units": {
            "roe": "percent",
            "gross_margin": "percent",
            "net_profit_margin": "percent",
            "total_revenue": "reported currency",
            "net_income": "reported currency",
            "operating_cash_flow": "reported currency",
        },
        "accounting_basis": "provider-reported HK financial statements; not normalized to A-share accounting",
    }
    if len(history) < periods:
        result["warnings"] = ["Requested financial history incomplete; only actual available periods returned"]
    return result


# Field codes from https://www.lixinger.com/api/open-api/html-doc/{cn,hk}/company/fs/non_financial.
# HK has no operating income, capex or D&A; the caller fills those from Yahoo.
DCF_FIELDS = {
    "cn": {
        "revenue": ("ps.toi",),
        "operating_income": ("ps.op",),
        "pretax_income": ("ps.tp",),
        "income_tax": ("ps.ite",),
        "operating_cash_flow": ("cfs.ncffoa",),
        "capital_expenditure": ("cfs.cpfpfiaolta",),
        "depreciation_amortization": ("cfs.daa",),
        "cash": ("cfs.bocaceatpe",),
        "total_debt": ("bs.stl", "bs.stbp", "bs.ncldwioy", "bs.ltl", "bs.bp"),
        "lease_liabilities": ("bs.ll",),
        "minority_interest": ("bs.etmsh",),
        "shares_outstanding": ("bs.tsc",),
    },
    "hk": {
        "revenue": ("ps.toi",),
        "pretax_income": ("ps.tp",),
        "income_tax": ("ps.ite",),
        "operating_cash_flow": ("cfs.ncffoa",),
        "cash": ("cfs.bocaceatpe",),
        "total_debt": (
            "bs.stl",
            "bs.stpl",
            "bs.stbp",
            "bs.stlcofcb",
            "bs.stsn",
            "bs.ltl",
            "bs.ltpl",
            "bs.sn",
            "bs.bp",
            "bs.ltlcofcb",
        ),
        "lease_liabilities": ("bs.stll", "bs.ltll"),
        "minority_interest": ("bs.etmsh",),
        "shares_outstanding": ("bs.tsc",),
    },
}
DCF_WARNINGS = {
    "cn": "Lixinger CN statements; operating_income is 营业利润 (includes investment and fair-value gains, not EBIT); "
    "total_debt sums borrowings, bonds and current non-current liabilities "
    "(the latter may include current lease liabilities); long-term leases are in lease_liabilities.",
    "hk": "Lixinger HK statements; total_debt sums borrowings, bonds, notes and convertibles and excludes leases.",
}


def dcf_inputs(market, code, periods):
    fields = DCF_FIELDS[market]
    metrics = [f"q.{field}.t" for names in fields.values() for field in names]
    rows = []
    for row in sorted(company_data(market, code, "fs", metrics), key=lambda r: r.get("date", ""), reverse=True):
        if row.get("reportType") != "annual_report" or not row.get("date") or row.get("stockCode", code) != code:
            continue
        entry = {"period_end": row["date"][:10], "currency": row.get("currency")}
        for key, names in fields.items():
            values = [value for name in names if (value := metric(row, name)) is not None]
            entry[key] = sum(values) if values else None
        rows.append(entry)
    if not rows:
        raise ValueError(f"Lixinger returned no annual {market} statements")
    return {"source": "lixinger", "periods": rows[:periods], "warnings": [DCF_WARNINGS[market]]}
