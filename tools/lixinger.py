"""Optional Lixinger HK non-financial datasets (same env token as index data)."""

import os
from datetime import timedelta

import requests
from _cache import DAY, cached
from trading_calendar import market_now


def hk_data(symbol, dataset, metrics):
    code = symbol.split(".")[0].zfill(5)
    end = market_now("HK").date()
    try:
        start = end.replace(year=end.year - 10)
    except ValueError:
        start = end.replace(year=end.year - 10, day=28) + timedelta(days=1)

    def fetch():
        response = requests.post(
            f"https://open.lixinger.com/api/hk/company/{dataset}/non_financial",
            json={
                "token": os.environ["LIXINGER_TOKEN"],
                "stockCodes": [code],
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metricsList": metrics,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 1 or not isinstance(payload.get("data"), list) or not payload["data"]:
            raise ValueError("Lixinger HK non-financial data unavailable; check token permissions/company type")
        return payload["data"]

    return cached(f"lixinger-hk:{dataset}:{code}:{end}:{','.join(metrics)}", DAY, fetch)


def financials_hk(symbol, periods, annual):
    fields = {
        "roe": "m.roe",
        "gross_margin": "ps.gp_m",
        "net_profit_margin": "ps.np_s_r",
        "total_revenue": "ps.toi",
        "net_income": "ps.np",
        "operating_cash_flow": "cfs.ncffoa",
    }
    data = hk_data(symbol, "fs", [f"q.{field}.t" for field in fields.values()])
    history = []
    for row in sorted(data, key=lambda r: r.get("date", ""), reverse=True):
        if annual and row.get("reportType") != "annual_report":
            continue
        if not row.get("date") or row.get("stockCode", symbol.split(".")[0].zfill(5)) != symbol.split(".")[0].zfill(5):
            continue
        entry = {
            "report_date": row["date"][:10],
            "disclosure_date": (row.get("reportDate") or "")[:10] or None,
            "report_type": row.get("reportType"),
            "currency": row.get("currency"),
            "source": "lixinger",
        }
        for key, field in fields.items():
            table, metric = field.split(".")
            value = row.get("q", {}).get(table, {}).get(metric, {}).get("t")
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
        "symbol": symbol,
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


# Field codes from https://www.lixinger.com/api/open-api/html-doc/hk/company/fs/non_financial
# (no operating income, capex or D&A there; those come from the Yahoo fill).
DCF_FIELDS = {
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
}


def dcf_inputs_hk(symbol, periods):
    metrics = [f"q.{field}.t" for fields in DCF_FIELDS.values() for field in fields]
    code = symbol.split(".")[0].zfill(5)
    rows = []
    for row in sorted(hk_data(symbol, "fs", metrics), key=lambda r: r.get("date", ""), reverse=True):
        if row.get("reportType") != "annual_report" or not row.get("date") or row.get("stockCode", code) != code:
            continue
        entry = {"period_end": row["date"][:10], "currency": row.get("currency")}
        for key, fields in DCF_FIELDS.items():
            values = [
                value
                for field in fields
                if (value := row.get("q", {}).get(field.split(".")[0], {}).get(field.split(".")[1], {}).get("t"))
                is not None
            ]
            entry[key] = sum(values) if values else None
        rows.append(entry)
    if not rows:
        raise ValueError("Lixinger returned no annual HK statements")
    return {
        "source": "lixinger",
        "periods": rows[:periods],
        "warnings": [
            "Lixinger HK statements; total_debt sums borrowings, bonds, notes and convertibles and excludes leases."
        ],
    }
