#!/usr/bin/env python3
"""Dated valuation, ownership, fund and dividend data for low-frequency research."""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cache import DAY, cached
from _subproc import scrub_secrets, socket_timeout, utf8_stdio
from stock_data import _A_INDEX_RE, _cn_code, _yf_hk_symbol, detect_market, normalize_stock_code


def records(frame):
    if frame is None:
        raise ValueError("provider returned no dataset")
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def dataset(key, fetch, ttl=DAY):
    return cached(key, ttl, lambda: records(fetch()))


def number(value):
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def iso(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.notna(parsed) and parsed.tzinfo is not None:
        parsed = parsed.tz_convert("Asia/Shanghai")
    return parsed.date().isoformat() if pd.notna(parsed) else None


def capture(result, key, fetch):
    try:
        result[key] = fetch()
    except Exception as exc:
        result.setdefault("warnings", []).append(f"{key}: unavailable ({type(exc).__name__})")


def stock_identity(symbol):
    market = detect_market(symbol)
    if _A_INDEX_RE.fullmatch(symbol):
        raise ValueError("explicit index symbol is not a stock; use index_valuation")
    if market == "A":
        return market, _cn_code(symbol)[2:]
    if market == "HK" and re.fullmatch(r"\d{1,5}\.HK", symbol, re.I):
        return market, symbol.split(".")[0].zfill(5)
    raise ValueError("an A/HK stock symbol is required")


def valuation_summary(history, field):
    dated = [r for r in history if r.get("date") and number(r.get(field)) is not None]
    dated.sort(key=lambda r: r["date"])
    valid = pd.Series([number(r[field]) for r in dated if number(r[field]) > 0], dtype=float)
    current = number(dated[-1][field]) if dated else None
    return {
        "current": current,
        "as_of": dated[-1]["date"] if dated else None,
        "samples": len(valid),
        "window_start": dated[0]["date"] if dated else None,
        "window_end": dated[-1]["date"] if dated else None,
        "percentile": round(float(valid.le(current).mean() * 100), 4)
        if len(valid) and current is not None and current > 0
        else None,
        "percentile_method": "percentage of positive observations <= current",
        "bands": {str(q): round(float(valid.quantile(q / 100)), 4) for q in [10, 25, 50, 75, 90]} if len(valid) else {},
    }


def valuation_history(args):
    import akshare as ak

    market, symbol = stock_identity(args.symbol)
    if not 1 <= args.years <= 10:
        raise ValueError("valuation history requires an A/HK symbol and years in 1..10")
    fetch = ak.stock_zh_valuation_baidu if market == "A" else ak.stock_hk_valuation_baidu
    period = "近一年" if args.years == 1 else "近三年" if args.years <= 3 else "近五年" if args.years <= 5 else "近十年"
    if market == "HK" and args.years > 3:
        period = "全部"
    result = {"symbol": args.symbol, "market": market, "source": "baidu", "requested_years": args.years}
    series = {}
    for field, indicator in [("pe_ttm", "市盈率(TTM)"), ("pb", "市净率")]:
        capture(
            series,
            field,
            lambda indicator=indicator: dataset(
                f"valuation:{market}:{symbol}:{indicator}:{period}",
                lambda: fetch(symbol=symbol, indicator=indicator, period=period),
            ),
        )
    merged = {}
    cutoff = (date.today() - timedelta(days=365.25 * args.years)).isoformat()
    for field in ("pe_ttm", "pb"):
        for row in series.get(field, []):
            stamp = iso(row.get("date"))
            if stamp and stamp >= cutoff:
                merged.setdefault(stamp, {"date": stamp})[field] = number(row.get("value"))
    history = sorted(merged.values(), key=lambda r: r["date"])
    if not history:
        raise ValueError("no valuation history available")
    result.update(history=history, metrics={field: valuation_summary(history, field) for field in ("pe_ttm", "pb")})
    if series.get("warnings"):
        result["warnings"] = series["warnings"]
    return result


INDEX_NAMES = {
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000906": "中证800",
    "000016": "上证50",
    "000985": "中证全指",
}


def index_valuation(args):
    import akshare as ak

    code = args.symbol.lower().removeprefix("sh").removeprefix("sz")
    code = next((code for code, name in INDEX_NAMES.items() if name == args.symbol), code).upper()
    name = INDEX_NAMES.get(code, args.symbol)
    if not 1 <= args.years <= 10:
        raise ValueError("years must be in 1..10")
    result = {"symbol": code, "name": name, "source": "legulegu", "requested_years": args.years}
    merged = {}
    for field, fn, column in [("pe_ttm", ak.stock_index_pe_lg, "滚动市盈率"), ("pb", ak.stock_index_pb_lg, "市净率")]:
        section = {}
        capture(
            section,
            "rows",
            lambda fn=fn, field=field: dataset(f"index-valuation:{name}:{field}", lambda: fn(symbol=name)),
        )
        for row in section.get("rows", []):
            stamp = iso(row.get("日期"))
            if stamp and stamp >= (date.today() - timedelta(days=365.25 * args.years)).isoformat():
                merged.setdefault(stamp, {"date": stamp})[field] = number(row.get(column))
        result.setdefault("warnings", []).extend(section.get("warnings", []))
    if not merged and re.fullmatch(r"\d{6}|H\d{5}", code, re.I):
        rows = dataset(f"csi-valuation:{code}", lambda: ak.stock_zh_index_value_csindex(symbol=code))
        merged = {
            iso(r["日期"]): {
                "date": iso(r["日期"]),
                "pe": number(r.get("市盈率1")),
                "dividend_yield": number(r.get("股息率1")),
            }
            for r in rows
            if iso(r.get("日期"))
        }
        result["source"] = "csindex"
        result.setdefault("warnings", []).append(
            "CSI provides recent PE based on total shares, not a full historical PE-TTM/PB series"
        )
        result["pe_basis"] = "CSI PE1, total shares"
    if not merged:
        raise ValueError("no index valuation data available")
    history = sorted(merged.values(), key=lambda r: r["date"])
    fields = ("pe", "dividend_yield") if result["source"] == "csindex" else ("pe_ttm", "pb")
    result.update(history=history, metrics={field: valuation_summary(history, field) for field in fields})
    if not result.get("warnings"):
        result.pop("warnings", None)
    return result


def earnings_calendar(args):
    import akshare as ak

    stamp = iso(args.report_date)
    suffix = {"03-31": "一季", "06-30": "半年报", "09-30": "三季", "12-31": "年报"}
    if not stamp or stamp[5:] not in suffix or not 1 <= args.top <= 500:
        raise ValueError("report_date must be a quarter end and top must be in 1..500")
    symbol = ""
    if args.symbol:
        market, symbol = stock_identity(args.symbol)
        if market != "A":
            raise ValueError("earnings calendar covers A shares only")
    result = {"report_date": stamp, "source": "cninfo/eastmoney"}
    capture(
        result,
        "disclosures",
        lambda: dataset(
            f"disclosure:{stamp}",
            lambda: ak.stock_report_disclosure(market="沪深京", period=stamp[:4] + suffix[stamp[5:]]),
        ),
    )
    capture(
        result, "forecasts", lambda: dataset(f"forecast:{stamp}", lambda: ak.stock_yjyg_em(date=stamp.replace("-", "")))
    )
    for key in ("disclosures", "forecasts"):
        rows = result.get(key, [])
        if symbol:
            rows = [r for r in rows if str(r.get("股票代码", "")).zfill(6) == symbol]
        output = []
        for row in rows:
            item = {"symbol": str(row.get("股票代码", "")).zfill(6), "name": row.get("股票简称")}
            if key == "disclosures":
                revised = [iso(row.get(c)) for c in ("首次预约", "初次变更", "二次变更", "三次变更")]
                item.update(
                    scheduled_date=next((d for d in reversed(revised) if d), None),
                    actual_date=iso(row.get("实际披露")),
                    original_scheduled_date=revised[0],
                    scheduled_date_revisions=[
                        {"revision": i, "scheduled_date": d} for i, d in enumerate(revised[1:], 1) if d
                    ],
                )
            else:
                item.update(
                    announcement_date=iso(row.get("公告日期")),
                    forecast_type=row.get("预告类型"),
                    indicator=row.get("预测指标"),
                    forecast_value=number(row.get("预测数值")),
                    change_pct=number(row.get("业绩变动幅度")),
                    reason=row.get("业绩变动原因"),
                )
            output.append(item)
        result[key + "_total"] = len(output)
        result[key] = output[: args.top]
    if result.get("warnings") and not result["disclosures"] and not result["forecasts"]:
        raise ValueError("earnings calendar sources unavailable")
    return result


def southbound_holdings(symbol):
    import requests

    def fetch():
        response = requests.get("https://www.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk", timeout=20)
        response.raise_for_status()
        tag = re.search(r"<input\b[^>]*\bname=[\"\']txtShareholdingDate[\"\'][^>]*>", response.text, re.I)
        stamp = iso(re.search(r"\bvalue=[\"\']([^\"\']+)", tag.group()).group(1)) if tag else None
        if not stamp:
            raise ValueError("HKEX disclosure date unavailable")
        frame = pd.read_html(StringIO(response.text), attrs={"id": "mutualmarket-result"})[0]
        output = []
        for row in frame.itertuples(index=False, name=None):
            cells = [str(v).split(":", 1)[-1].strip() for v in row]
            code = cells[0].zfill(5)
            output.append(
                {
                    "symbol": code + ".HK",
                    "name": cells[1],
                    "shares": number(cells[2].replace(",", "")),
                    "holding_pct": number(cells[3].rstrip("%")),
                }
            )
        return {"as_of": stamp, "rows": output}

    result = cached("hkex-southbound", DAY, fetch)
    code = symbol.split(".")[0].zfill(5) + ".HK"
    hit = next((r for r in result["rows"] if r["symbol"] == code), None)
    return {
        "as_of": result["as_of"],
        "source": "HKEX Stock Connect shareholding",
        **(
            hit
            or {
                "symbol": code,
                "shares": None,
                "holding_pct": None,
                "note": "not present in published Stock Connect list",
            }
        ),
    }


def shareholder_structure(args):
    import akshare as ak

    market, symbol = stock_identity(args.symbol)
    result = {"symbol": args.symbol, "market": market}
    if market == "HK":
        result["southbound"] = southbound_holdings(args.symbol)
        result["note"] = (
            "Southbound holdings are dated CCASS Stock Connect records; not a complete beneficial-owner register"
        )
        return result
    stamp = iso(args.report_date)
    if args.report_date and (not stamp or stamp[5:] not in ("03-31", "06-30", "09-30", "12-31")):
        raise ValueError("report_date must be a quarter end")
    capture(
        result,
        "shareholder_counts",
        lambda: dataset(f"holders:{symbol}", lambda: ak.stock_zh_a_gdhs_detail_em(symbol=symbol)),
    )
    rows = result.get("shareholder_counts", [])
    result["shareholder_counts"] = sorted(
        [
            {
                "as_of": iso(r.get("股东户数统计截止日")),
                "announcement_date": iso(r.get("股东户数公告日期")),
                "holders": number(r.get("股东户数-本次")),
                "change": number(r.get("股东户数-增减")),
                "change_pct": number(r.get("股东户数-增减比例")),
            }
            for r in rows
        ],
        key=lambda r: r["as_of"] or "",
        reverse=True,
    )
    if not stamp:
        from stock_data import cmd_financials

        financials = cmd_financials(argparse.Namespace(symbol=symbol, periods=1, annual=False))
        stamp = iso(financials.get("report_date"))
    if not stamp or stamp[5:] not in ("03-31", "06-30", "09-30", "12-31"):
        result.setdefault("warnings", []).append(
            "top_float_shareholders: report_date required when latest disclosed financial report is unavailable"
        )
    else:
        result["report_date"] = stamp
        capture(
            result,
            "top_float_shareholders",
            lambda: dataset(
                f"top-float:{symbol}:{stamp}",
                lambda: ak.stock_gdfx_free_top_10_em(symbol=_cn_code(symbol), date=stamp.replace("-", "")),
            ),
        )
        result["top_float_shareholders"] = [
            {
                "name": r.get("股东名称"),
                "shares": number(r.get("持股数")),
                "float_holding_pct": number(r.get("占总流通股本持股比例")),
                "change": r.get("增减"),
                "change_pct": number(r.get("变动比率")),
                "report_date": stamp,
            }
            for r in result.get("top_float_shareholders", [])
        ]
        nominee = [r for r in result["top_float_shareholders"] if (r["name"] or "").strip() == "香港中央结算有限公司"]
        result["northbound"] = {
            "as_of": stamp,
            "source": "disclosed top float shareholder register",
            "shares": sum(r["shares"] for r in nominee if r["shares"] is not None) if nominee else None,
            "note": "HKSCC nominee disclosure; absence from top ten is not zero holdings. Daily northbound stock holding disclosure is no longer assumed.",
        }
    if not result["shareholder_counts"] and not result.get("top_float_shareholders"):
        raise ValueError("shareholder sources unavailable")
    return result


def dividend_ranking(report_date=""):
    import akshare as ak

    stamp = iso(report_date) if report_date else f"{date.today().year - 1}-12-31"
    if not stamp or stamp[5:] not in ("03-31", "06-30", "09-30", "12-31"):
        raise ValueError("dividend report_date must be a quarter end")
    rows = dataset(f"dividend-ranking:{stamp}", lambda: ak.stock_fhps_em(date=stamp.replace("-", "")))
    return {
        "report_date": stamp,
        "source": "eastmoney",
        "yield_basis": "provider report-period cash dividend yield, percent",
        "items": [
            {
                "symbol": str(r.get("代码", "")).zfill(6),
                "name": r.get("名称"),
                "dividend_yield": number(r.get("现金分红-股息率")),
                "ex_date": iso(r.get("除权除息日")),
                "announcement_date": iso(r.get("最新公告日期")),
                "status": r.get("方案进度"),
            }
            for r in rows
            if r.get("方案进度") == "实施分配" and number(r.get("现金分红-股息率")) is not None
        ],
    }


def dividends(args):
    if not 1 <= args.years <= 20 or not 1 <= args.top <= 500:
        raise ValueError("years must be in 1..20 and top in 1..500")
    if args.mode == "ranking":
        if args.symbol:
            raise ValueError("ranking covers all A shares; omit symbol")
        result = dividend_ranking(args.report_date)
        result["items"] = sorted(result["items"], key=lambda r: r["dividend_yield"], reverse=True)[: args.top]
        return result
    market, symbol = stock_identity(args.symbol)
    import akshare as ak
    from _subproc import run_tool

    today = date.today()
    result = {
        "symbol": args.symbol,
        "market": market,
        "as_of": today.isoformat(),
        "yield_basis": "cash paid per share in trailing 365 days / latest price, before tax; nominal source cash amounts",
    }
    if market == "A":
        symbol = _cn_code(args.symbol)[2:]
        rows = dataset(f"dividends:{symbol}", lambda: ak.stock_fhps_detail_em(symbol=symbol))
        events = [
            {
                "report_date": iso(r.get("报告期")),
                "ex_date": iso(r.get("除权除息日")),
                "record_date": iso(r.get("股权登记日")),
                "announcement_date": iso(r.get("最新公告日期")),
                "cash_per_share": number(r.get("现金分红-现金分红比例")) / 10
                if number(r.get("现金分红-现金分红比例")) is not None
                else None,
                "status": r.get("方案进度"),
            }
            for r in rows
        ]
        if args.mode == "calendar":
            return {
                "symbol": args.symbol,
                "market": market,
                "source": "eastmoney",
                "as_of": today.isoformat(),
                "calendar": [r for r in events if r.get("ex_date") and r["ex_date"] > today.isoformat()],
            }
        quote = run_tool("stock_data.py", ["quote", symbol]) or {}
        price = number(quote.get("price"))
        result.update(source="eastmoney", currency="CNY", price=price, price_as_of=quote.get("as_of"))
    else:
        import yfinance as yf

        symbol = args.symbol.split(".")[0].zfill(5)
        rows = dataset(f"hk-dividend-calendar:{symbol}", lambda: ak.stock_hk_dividend_payout_em(symbol=symbol))
        calendar = [
            {
                "ex_date": iso(r.get("除净日")),
                "payment_date": iso(r.get("发放日")),
                "announcement_date": iso(r.get("最新公告日期")),
                "plan": r.get("分红方案"),
                "fiscal_year": r.get("财政年度"),
            }
            for r in rows
        ]
        if args.mode == "calendar":
            return {
                "symbol": args.symbol,
                "market": market,
                "source": "eastmoney",
                "as_of": today.isoformat(),
                "calendar": [r for r in calendar if r.get("ex_date") and r["ex_date"] > today.isoformat()],
            }
        ticker = yf.Ticker(_yf_hk_symbol(args.symbol))

        def cash_history():
            raw = ticker.dividends
            frame = (
                raw.to_frame("cash_per_share")
                if isinstance(raw, pd.Series)
                else raw.rename(columns={"Dividends": "cash_per_share"})
            )
            return frame.reset_index()

        paid = dataset(f"hk-dividend-cash:{symbol}", cash_history)
        info = ticker.info
        events = [
            {
                "ex_date": iso(r.get("Date")),
                "cash_per_share": number(r.get("cash_per_share")),
                "currency": r.get("currency") or info.get("currency"),
                "status": "paid",
            }
            for r in paid
        ]
        price = number(info.get("currentPrice") or info.get("regularMarketPrice"))
        result.update(source="yfinance cash / eastmoney calendar", currency=info.get("currency"), price=price)
        result["calendar"] = [r for r in calendar if r["ex_date"] and r["ex_date"] > today.isoformat()]
    cutoff = (today - timedelta(days=365.25 * args.years)).isoformat()
    events = sorted(
        [r for r in events if (r.get("ex_date") or r.get("report_date") or "") >= cutoff],
        key=lambda r: r.get("ex_date") or r.get("report_date") or "",
        reverse=True,
    )
    implemented = [
        r
        for r in events
        if r["status"] in ("实施分配", "paid")
        and r.get("ex_date")
        and r["ex_date"] <= today.isoformat()
        and r["cash_per_share"] is not None
        and r["cash_per_share"] > 0
    ]
    ttm = [r for r in implemented if r["ex_date"] > (today - timedelta(days=365)).isoformat()]
    cash_by_currency = {}
    for row in ttm:
        currency = row.get("currency") or result.get("currency") or "unknown"
        cash_by_currency[currency] = round(cash_by_currency.get(currency, 0) + row["cash_per_share"], 6)
    currency_match = result.get("currency") and all(currency == result["currency"] for currency in cash_by_currency)
    cash = sum(r["cash_per_share"] for r in ttm) if currency_match and events else None
    if not events:
        result.setdefault("warnings", []).append("no dividend history returned; absence is not interpreted as zero")
    elif not currency_match:
        result.setdefault("warnings", []).append(
            "cash dividend and quote currencies differ or are unknown; no FX conversion was assumed"
        )
    years = {int(r["ex_date"][:4]) for r in implemented}
    anchor = today.year if today.year in years else today.year - 1
    consecutive = 0
    while anchor - consecutive in years:
        consecutive += 1
    result.update(
        history=events,
        cash_per_share_ttm=round(cash, 6) if cash is not None else None,
        cash_per_share_ttm_by_currency=cash_by_currency,
        dividend_yield_ttm=round(cash / price * 100, 4) if cash is not None and price and price > 0 else None,
        consecutive_payment_years=consecutive if events else None,
        consecutive_years_end=anchor,
        history_years_requested=args.years,
    )
    if market == "A":
        result["calendar"] = [r for r in events if r.get("ex_date") and r["ex_date"] > today.isoformat()]
    return result


def tracking_error(nav, benchmark):
    if not nav or not benchmark:
        return {
            "annualized_tracking_error_pct": None,
            "observations": 0,
            "note": "fund NAV or benchmark history unavailable",
        }
    fund = pd.DataFrame(nav).drop_duplicates("date").set_index("date").sort_index()
    index = pd.DataFrame(benchmark).drop_duplicates("date").set_index("date").sort_index()
    fund_returns = pd.to_numeric(fund["daily_return_pct"], errors="coerce") / 100
    close = pd.to_numeric(index["close"], errors="coerce")
    index_returns = close.pct_change(fill_method=None)
    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XSHG")
        previous = {
            stamp: calendar.previous_session(stamp).date().isoformat()
            for stamp in fund.index.union(index.index)
            if calendar.is_session(stamp)
        }
    except Exception:
        return {"annualized_tracking_error_pct": None, "observations": 0, "note": "ETF trading calendar unavailable"}
    matched = (
        pd.concat(
            [
                fund_returns.rename("fund"),
                index_returns.rename("benchmark"),
                fund.index.to_series().shift().rename("fund_previous"),
                index.index.to_series().shift().rename("benchmark_previous"),
                pd.Series(previous).rename("session_previous"),
            ],
            axis=1,
        )
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    matched = matched[
        (matched["fund_previous"] == matched["session_previous"])
        & (matched["benchmark_previous"] == matched["session_previous"])
    ]
    active = matched["fund"] - matched["benchmark"]
    return {
        "annualized_tracking_error_pct": round(float(active.std(ddof=1) * np.sqrt(252) * 100), 4)
        if len(active) >= 2
        else None,
        "observations": len(active),
        "window_start": matched.index[0] if len(active) else None,
        "window_end": matched.index[-1] if len(active) else None,
        "basis": "daily published NAV growth minus benchmark close return; sample std, 252 trading days; only matching consecutive ETF trading-session intervals, no missing-date fill",
    }


def fund_size_history(symbol):
    import requests

    def fetch():
        response = requests.get(f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js", timeout=15)
        response.raise_for_status()
        match = re.search(r"\bData_fluctuationScale\s*=\s*(.*?);", response.text, re.S)
        if not match:
            raise ValueError("fund scale history unavailable")
        data = json.loads(match.group(1))
        if len(data["categories"]) != len(data["series"]):
            raise ValueError("fund scale dates and observations do not match")
        return [
            {"date": iso(stamp), "aum_cny": number(row.get("y")) * 1e8}
            for stamp, row in zip(data["categories"], data["series"], strict=True)
            if iso(stamp) and number(row.get("y")) is not None
        ]

    return cached(f"etf-size:{symbol}", DAY, fetch)


def etf_details(args):
    import akshare as ak
    from _subproc import run_tool

    symbol = _cn_code(args.symbol)[2:]
    if detect_market(args.symbol) != "A" or not normalize_stock_code(symbol)["is_etf"] or not 10 <= args.days <= 252:
        raise ValueError("ETF details requires an A-share ETF code and days in 10..252")
    result = {
        "symbol": symbol,
        "market": "A",
        "source": "eastmoney",
        "holdings_basis": "latest disclosed fund portfolio, not a complete live creation/redemption basket",
    }
    end = date.today()
    start = end - timedelta(days=args.days * 2)
    capture(
        result,
        "nav_history",
        lambda: dataset(
            f"etf-nav:{symbol}:{args.days}",
            lambda: ak.fund_etf_fund_info_em(
                fund=symbol, start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d")
            ),
        ),
    )
    nav = sorted(
        [
            {
                "date": iso(r.get("净值日期")),
                "nav": number(r.get("单位净值")),
                "daily_return_pct": number(r.get("日增长率")),
            }
            for r in result.get("nav_history", [])
            if iso(r.get("净值日期"))
        ],
        key=lambda r: r["date"],
    )[-args.days :]
    result["nav_history"] = nav
    latest = nav[-1] if nav else {}
    result.update(nav=latest.get("nav"), nav_date=latest.get("date"))
    quote = run_tool("stock_data.py", ["quote", symbol]) or {}
    quote_date = iso(quote.get("as_of"))
    price = number(quote.get("price"))
    result.update(
        price=price,
        price_as_of=quote.get("as_of"),
        fund_shares=number(quote.get("fund_shares")),
        iopv=number(quote.get("iopv")),
        iopv_premium_discount_pct=number(quote.get("premium_discount_rate")),
        premium_discount_pct=round((price / latest["nav"] - 1) * 100, 4)
        if quote_date
        and quote_date == latest.get("date")
        and price is not None
        and latest.get("nav")
        and latest["nav"] > 0
        else None,
        premium_basis="last price / published NAV only when dates match; positive=premium",
    )
    if result["premium_discount_pct"] is None:
        result.setdefault("warnings", []).append("premium: quote/NAV dates differ or date/value unavailable")
    capture(result, "size_history", lambda: fund_size_history(symbol))
    sizes = sorted(result.get("size_history", []), key=lambda r: r["date"])
    result["size_history"] = sizes
    result["size_change_pct"] = (
        round((sizes[-1]["aum_cny"] / sizes[-2]["aum_cny"] - 1) * 100, 4)
        if len(sizes) >= 2 and sizes[-2]["aum_cny"] > 0
        else None
    )
    capture(
        result,
        "holdings",
        lambda: dataset(f"etf-holdings:{symbol}", lambda: ak.fund_portfolio_hold_em(symbol=symbol, date="")),
    )
    rows = result.get("holdings", [])
    if rows:
        latest_quarter = max(str(r.get("季度", "")) for r in rows)
        result["holdings"] = [
            {
                "code": str(r.get("股票代码", "")),
                "name": r.get("股票名称"),
                "weight_pct": number(r.get("占净值比例")),
                "report_period": r.get("季度"),
            }
            for r in rows
            if str(r.get("季度", "")) == latest_quarter
        ]
    result["holdings_code_basis"] = "original reported security codes; foreign exchange/market is not inferred"
    if args.benchmark:
        benchmark = "sh" + args.benchmark if args.benchmark in INDEX_NAMES else args.benchmark
        benchmark_rows = run_tool("stock_data.py", ["kline", benchmark, "--count", str(args.days + 5)])
        result["tracking_error"] = tracking_error(nav, benchmark_rows if isinstance(benchmark_rows, list) else [])
        result["tracking_error"]["benchmark"] = benchmark
        if benchmark.lower().startswith(("sh", "sz")):
            code = benchmark[2:]
            capture(
                result,
                "benchmark_constituents",
                lambda: dataset(f"index-constituents:{code}", lambda: ak.index_stock_cons_csindex(symbol=code)),
            )
            if "benchmark_constituents" in result:
                result["benchmark_constituents"] = [
                    {
                        "code": r.get("成分券代码"),
                        "name": r.get("成分券名称"),
                        "exchange": r.get("交易所名称"),
                        "as_of": iso(r.get("日期")),
                    }
                    for r in result["benchmark_constituents"]
                ]
    else:
        result["tracking_error"] = {
            "annualized_tracking_error_pct": None,
            "note": "provide benchmark to compute tracking error; benchmark is never guessed",
        }
    if not nav and not sizes and not result.get("holdings"):
        raise ValueError("ETF detail sources unavailable")
    return result


def main():
    parser = argparse.ArgumentParser(description="Value-investing datasets and dated historical context")
    sub = parser.add_subparsers(dest="command")
    for command in ("valuation_history", "index_valuation"):
        p = sub.add_parser(command)
        p.add_argument("symbol")
        p.add_argument("--years", type=int, default=5)
    p = sub.add_parser("earnings_calendar")
    p.add_argument("--symbol", default="")
    p.add_argument("--report-date", required=True)
    p.add_argument("--top", type=int, default=50)
    p = sub.add_parser("shareholder_structure")
    p.add_argument("symbol")
    p.add_argument("--report-date", default="")
    p = sub.add_parser("etf_details")
    p.add_argument("symbol")
    p.add_argument("--benchmark", default="")
    p.add_argument("--days", type=int, default=60)
    p = sub.add_parser("dividends")
    p.add_argument("--symbol", default="")
    p.add_argument("--mode", choices=["history", "calendar", "ranking"], default="history")
    p.add_argument("--report-date", default="")
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--top", type=int, default=20)
    try:
        args = parser.parse_args()
        handlers = {
            "valuation_history": valuation_history,
            "index_valuation": index_valuation,
            "earnings_calendar": earnings_calendar,
            "shareholder_structure": shareholder_structure,
            "etf_details": etf_details,
            "dividends": dividends,
        }
        if args.command not in handlers:
            raise ValueError("a subcommand is required")
        with socket_timeout(20):
            result = handlers[args.command](args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except SystemExit as exc:
        if exc.code:
            print(json.dumps({"error": "invalid CLI arguments"}))
        raise
    except Exception as exc:
        print(json.dumps({"error": scrub_secrets(str(exc))}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    utf8_stdio()
    main()
