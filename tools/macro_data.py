#!/usr/bin/env python3
"""Macro, interest-rate, FX, and market risk-appetite anchors (keyless by default).

Provides risk-free yield anchors (US/CN 10Y, US 2Y), FX rates (DXY, USD/CNH, USD/HKD,
HKD/CNY), and risk-appetite proxies (VIX, Gold, WTI Crude) for DCF valuation,
cross-currency portfolio risk, and market review.
"""

import argparse
import csv
import io
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cache import cached  # noqa: E402
from _subproc import error_message, json_safe, utf8_stdio  # noqa: E402

MACRO_TTL = 3600  # 1 hour cache for daily macro anchors

GROUPS = {
    "rates": ("us_10y", "us_2y", "cn_10y"),
    "fx": ("dxy", "usdcnh", "usdhkd", "hkdcny"),
    "risk": ("vix", "gold", "crude_oil"),
}
ALL_KEYS = GROUPS["rates"] + GROUPS["fx"] + GROUPS["risk"]

YF_SPECS = {
    "us_10y": ("^TNX", "US 10Y Treasury Yield", "percent", 0.1),
    "dxy": ("DX-Y.NYB", "US Dollar Index (DXY)", "index", 1.0),
    "usdcnh": ("CNH=X", "USD/CNH Exchange Rate", "rate", 1.0),
    "usdhkd": ("HKD=X", "USD/HKD Exchange Rate", "rate", 1.0),
    "vix": ("^VIX", "CBOE Volatility Index (VIX)", "index", 1.0),
    "gold": ("GC=F", "COMEX Gold Futures", "usd_per_oz", 1.0),
    "crude_oil": ("CL=F", "WTI Crude Oil Futures", "usd_per_bbl", 1.0),
}

FRED_SPECS = {
    "us_10y": ("DGS10", "US 10Y Treasury Yield", "percent"),
    "us_2y": ("DGS2", "US 2Y Treasury Yield", "percent"),
}


def _finite(val) -> float | None:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _summarize_series(
    points: list[tuple[str, float]],
    *,
    name: str,
    unit: str,
    source: str,
    symbol: str | None = None,
) -> dict:
    clean = [(d, v) for d, v in points if d and _finite(v) is not None]
    clean.sort(key=lambda item: item[0])
    if not clean:
        raise ValueError(f"empty series for {name}")
    values = [v for _, v in clean]
    latest = values[-1]
    start_val = values[0]
    prev_val = values[-2] if len(values) >= 2 else None
    change = round(latest - prev_val, 4) if prev_val is not None else None
    change_pct = round((latest - prev_val) / prev_val * 100, 4) if prev_val not in (None, 0) else None
    window_change = round(latest - start_val, 4)
    out = {
        "name": name,
        "unit": unit,
        "source": source,
        "latest": round(latest, 4),
        "as_of": clean[-1][0],
        "change_1d": change,
        "change_1d_pct": change_pct if unit != "percent" else None,
        "window_start": clean[0][0],
        "window_change": window_change,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "observations": len(values),
    }
    if symbol:
        out["symbol"] = symbol
    return out


def _fetch_yf_series(ticker: str, days: int, scale: float = 1.0) -> list[tuple[str, float]]:
    import yfinance as yf

    period = "1mo" if days <= 25 else "3mo" if days <= 75 else "6mo" if days <= 160 else "1y"
    df = yf.download(ticker, period=period, auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise ValueError(f"yfinance returned empty history for {ticker}")
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    points = []
    for idx, val in close.dropna().items():
        v = _finite(val)
        if v is not None and v > 0:
            points.append((str(idx)[:10], v * scale))
    if not points:
        raise ValueError(f"yfinance returned no valid closes for {ticker}")
    return points[-max(days, 2) :]


def _fetch_fred_series(series_id: str, days: int) -> list[tuple[str, float]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = requests.get(url, timeout=15, headers={"User-Agent": "stock-analysis-plugin"})
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    points = []
    for row in reader:
        date_str = row.get("observation_date") or row.get("DATE")
        val = _finite(row.get(series_id))
        if date_str and val is not None:
            points.append((date_str[:10], val))
    if not points:
        raise ValueError(f"FRED returned no observations for {series_id}")
    return points[-max(days, 2) :]


def _fetch_cn_us_yields_akshare(days: int) -> dict[str, list[tuple[str, float]]]:
    import akshare as ak

    df = ak.bond_zh_us_rate()
    if df is None or df.empty:
        raise ValueError("akshare bond_zh_us_rate returned empty data")
    date_col = next((c for c in df.columns if "日期" in str(c)), df.columns[0])
    cn10_col = next((c for c in df.columns if "中国国债收益率10年" in str(c)), None)
    us10_col = next((c for c in df.columns if "美国国债收益率10年" in str(c)), None)
    us2_col = next((c for c in df.columns if "美国国债收益率2年" in str(c)), None)
    out: dict[str, list[tuple[str, float]]] = {}
    for key, col in (("cn_10y", cn10_col), ("us_10y", us10_col), ("us_2y", us2_col)):
        if not col:
            continue
        pts = []
        for _, row in df[[date_col, col]].dropna().tail(max(days, 5)).iterrows():
            v = _finite(row[col])
            if v is not None:
                pts.append((str(row[date_col])[:10], v))
        if pts:
            out[key] = pts[-max(days, 2) :]
    if not out:
        raise ValueError("akshare bond_zh_us_rate had no usable yield columns")
    return out


def _fetch_anchor_uncached(key: str, days: int, bond_cache: dict | None = None) -> dict:
    if key == "cn_10y":
        bonds = bond_cache if bond_cache is not None else _fetch_cn_us_yields_akshare(days)
        pts = bonds.get("cn_10y")
        if not pts:
            raise ValueError("CN 10Y yield unavailable")
        return _summarize_series(pts, name="China 10Y Government Bond Yield", unit="percent", source="akshare_bond")

    if key == "us_2y":
        errors = []
        try:
            pts = _fetch_fred_series("DGS2", days)
            return _summarize_series(pts, name="US 2Y Treasury Yield", unit="percent", source="fred", symbol="DGS2")
        except Exception as exc:
            errors.append(f"fred: {error_message(exc, 100)}")
        try:
            bonds = bond_cache if bond_cache is not None else _fetch_cn_us_yields_akshare(days)
            pts = bonds.get("us_2y")
            if pts:
                return _summarize_series(pts, name="US 2Y Treasury Yield", unit="percent", source="akshare_bond")
        except Exception as exc:
            errors.append(f"akshare: {error_message(exc, 100)}")
        raise ValueError("; ".join(errors) or "US 2Y yield unavailable")

    if key == "us_10y":
        errors = []
        ticker, name, unit, scale = YF_SPECS["us_10y"]
        try:
            pts = _fetch_yf_series(ticker, days, scale=scale)
            return _summarize_series(pts, name=name, unit=unit, source="yfinance", symbol=ticker)
        except Exception as exc:
            errors.append(f"yfinance: {error_message(exc, 100)}")
        try:
            pts = _fetch_fred_series("DGS10", days)
            return _summarize_series(pts, name=name, unit=unit, source="fred", symbol="DGS10")
        except Exception as exc:
            errors.append(f"fred: {error_message(exc, 100)}")
        try:
            bonds = bond_cache if bond_cache is not None else _fetch_cn_us_yields_akshare(days)
            pts = bonds.get("us_10y")
            if pts:
                return _summarize_series(pts, name=name, unit=unit, source="akshare_bond")
        except Exception as exc:
            errors.append(f"akshare: {error_message(exc, 100)}")
        raise ValueError("; ".join(errors))

    if key == "usdcnh":
        errors = []
        for ticker in ("CNH=X", "CNY=X"):
            try:
                pts = _fetch_yf_series(ticker, days)
                return _summarize_series(
                    pts, name="USD/CNH Exchange Rate", unit="rate", source="yfinance", symbol=ticker
                )
            except Exception as exc:
                errors.append(f"{ticker}: {error_message(exc, 100)}")
        raise ValueError("; ".join(errors))

    if key in YF_SPECS:
        ticker, name, unit, scale = YF_SPECS[key]
        pts = _fetch_yf_series(ticker, days, scale=scale)
        return _summarize_series(pts, name=name, unit=unit, source="yfinance", symbol=ticker)

    raise ValueError(f"unknown macro anchor: {key}")


def get_macro_anchors(group: str = "all", days: int = 30) -> dict:
    group = (group or "all").lower()
    if group != "all" and group not in GROUPS:
        return {"error": f"Unknown macro group: {group}. Expected one of all, rates, fx, risk"}
    if isinstance(days, bool) or not isinstance(days, int) or days < 2 or days > 365:
        return {"error": "days must be an integer between 2 and 365"}

    keys = list(ALL_KEYS if group == "all" else GROUPS[group])
    fetch_keys = [k for k in keys if k != "hkdcny"]

    # Fetch akshare CN/US bond table once if needed by rates group
    bond_cache = None
    if any(k in fetch_keys for k in ("cn_10y", "us_10y", "us_2y")):
        try:
            bond_cache = cached(f"macro_bonds_{days}", MACRO_TTL, lambda: _fetch_cn_us_yields_akshare(days))
        except Exception:
            bond_cache = {}

    anchors: dict[str, dict] = {}
    missing: list[str] = []
    warnings: list[str] = []

    def load_one(key: str):
        return cached(
            f"macro_{key}_{days}",
            MACRO_TTL,
            lambda: _fetch_anchor_uncached(key, days, bond_cache=bond_cache),
        )

    with ThreadPoolExecutor(max_workers=min(len(fetch_keys), 6) or 1) as pool:
        futures = {key: pool.submit(load_one, key) for key in fetch_keys}
        for key, future in futures.items():
            try:
                anchors[key] = future.result()
            except Exception as exc:
                missing.append(key)
                warnings.append(f"{key} unavailable: {error_message(exc, 150)}")

    # Derive HKD/CNY from USD/CNH and USD/HKD if requested
    if "hkdcny" in keys:
        cnh = (anchors.get("usdcnh") or {}).get("latest")
        hkd = (anchors.get("usdhkd") or {}).get("latest")
        if _finite(cnh) and _finite(hkd) and hkd > 0:
            anchors["hkdcny"] = {
                "name": "HKD/CNY Implied Cross Rate",
                "unit": "rate",
                "source": "derived(usdcnh/usdhkd)",
                "latest": round(cnh / hkd, 4),
                "as_of": anchors["usdcnh"].get("as_of") or anchors["usdhkd"].get("as_of"),
            }
        else:
            missing.append("hkdcny")
            warnings.append("hkdcny unavailable: requires both usdcnh and usdhkd")

    if not anchors:
        return {"error": f"All requested macro anchors unavailable ({', '.join(missing)})", "warnings": warnings}

    derived: dict[str, float | None] = {}
    us10 = (anchors.get("us_10y") or {}).get("latest")
    cn10 = (anchors.get("cn_10y") or {}).get("latest")
    us2 = (anchors.get("us_2y") or {}).get("latest")
    if _finite(us10) is not None and _finite(cn10) is not None:
        derived["us_cn_10y_spread_bp"] = round((us10 - cn10) * 100, 1)
    if _finite(us10) is not None and _finite(us2) is not None:
        derived["us_10y_2y_spread_bp"] = round((us10 - us2) * 100, 1)

    return {
        "meta": {
            "provider": "macro_data",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "delayed_or_daily",
            "fallback_used": any(
                a.get("source") in ("fred", "akshare_bond") for k, a in anchors.items() if k == "us_10y"
            ),
            "warnings": warnings,
        },
        "group": group,
        "days": days,
        "anchors": anchors,
        "derived": derived,
        "missing": missing,
        "notes": [
            "Yields are expressed in percent (e.g. 4.25 means 4.25%); spreads are in basis points (bp).",
            "Suitable as risk-free rate, FX, and risk-appetite references for DCF valuation, portfolio risk, and market review.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Macro, rates, FX, and volatility anchors")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("anchors")
    p.add_argument("--group", default="all", choices=["all", "rates", "fx", "risk"])
    p.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    result = get_macro_anchors(group=args.group, days=args.days)
    print(json.dumps(json_safe(result), ensure_ascii=False, indent=2, allow_nan=False))
    if isinstance(result, dict) and "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    utf8_stdio()
    main()
