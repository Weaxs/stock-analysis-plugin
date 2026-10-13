#!/usr/bin/env python3
"""Dated valuation, ownership, fund and dividend data for low-frequency research."""

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta
from html import unescape
from io import StringIO
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

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


def dataset(key, fetch):
    return cached(key, DAY, lambda: records(fetch()))


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
    warning = None
    if market == "HK":
        if os.environ.get("LIXINGER_TOKEN"):
            try:
                from lixinger import hk_data

                rows = hk_data(args.symbol, "fundamental", ["pe_ttm", "pb"])
                cutoff = (date.today() - pd.DateOffset(years=args.years)).date().isoformat()
                history = sorted(
                    [
                        {"date": iso(r.get("date")), "pe_ttm": number(r.get("pe_ttm")), "pb": number(r.get("pb"))}
                        for r in rows
                        if iso(r.get("date")) and iso(r.get("date")) >= cutoff
                    ],
                    key=lambda r: r["date"],
                )
                if not history or not all(any(r[field] is not None for r in history) for field in ("pe_ttm", "pb")):
                    raise ValueError("Lixinger HK valuation history incomplete")
                return {
                    "symbol": args.symbol,
                    "market": market,
                    "source": "lixinger",
                    "requested_years": args.years,
                    "history": history,
                    "metrics": {field: valuation_summary(history, field) for field in ("pe_ttm", "pb")},
                }
            except Exception:
                warning = "Lixinger HK unavailable; Baidu history retained without mixing sources"
        else:
            warning = "LIXINGER_TOKEN not configured; HK history limited to available Baidu data"
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
    if warning:
        result.setdefault("warnings", []).append(warning)
    return result


INDEX_NAMES = {
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000906": "中证800",
    "000016": "上证50",
    "000985": "中证全指",
}


def lixinger_index_history(code, start, end):
    import requests

    def fetch():
        response = requests.post(
            "https://open.lixinger.com/api/cn/index/fundamental",
            json={
                "token": os.environ["LIXINGER_TOKEN"],
                "stockCodes": [code],
                "startDate": start,
                "endDate": end,
                "metricsList": ["pe_ttm.mcw", "pb.mcw"],
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 1:
            raise ValueError("Lixinger index data unavailable; check token permissions")
        rows = [
            {"date": iso(r.get("date")), "pe_ttm": number(r.get("pe_ttm.mcw")), "pb": number(r.get("pb.mcw"))}
            for r in payload.get("data", [])
            if r.get("stockCode") == code
        ]
        if not rows or not any(r["date"] and (r["pe_ttm"] is not None or r["pb"] is not None) for r in rows):
            raise ValueError("Lixinger returned no usable index history")
        return rows

    return cached(f"lixinger-index:{code}:{start}:{end}", DAY, fetch)


def index_valuation(args):
    import akshare as ak

    code = args.symbol.lower().removeprefix("sh").removeprefix("sz")
    code = next((code for code, name in INDEX_NAMES.items() if name == args.symbol), code).upper()
    name = INDEX_NAMES.get(code, args.symbol)
    if not 1 <= args.years <= 10:
        raise ValueError("years must be in 1..10")
    result = {"symbol": code, "name": name, "source": "legulegu", "requested_years": args.years}
    merged = {}
    cutoff = (pd.Timestamp(date.today()) - pd.DateOffset(years=args.years)).date().isoformat()
    for field, fn, column in [("pe_ttm", ak.stock_index_pe_lg, "滚动市盈率"), ("pb", ak.stock_index_pb_lg, "市净率")]:
        section = {}
        capture(
            section,
            "rows",
            lambda fn=fn, field=field: dataset(f"index-valuation:{name}:{field}", lambda: fn(symbol=name)),
        )
        for row in section.get("rows", []):
            stamp = iso(row.get("日期"))
            if stamp and cutoff <= stamp <= date.today().isoformat():
                merged.setdefault(stamp, {"date": stamp})[field] = number(row.get(column))
        result.setdefault("warnings", []).extend(section.get("warnings", []))
    if not any(number(r.get("pe_ttm")) is not None for r in merged.values()) and re.fullmatch(
        r"\d{6}|H\d{5}", code, re.I
    ):
        section = {}
        capture(
            section,
            "history",
            lambda: dataset(
                f"csi-history:{code}:{args.years}",
                lambda: ak.stock_zh_index_hist_csindex(
                    symbol=code, start_date=cutoff.replace("-", ""), end_date=date.today().strftime("%Y%m%d")
                ),
            ),
        )
        for row in section.get("history", []):
            stamp = iso(row.get("日期"))
            pe = number(row.get("滚动市盈率"))
            if stamp and cutoff <= stamp <= date.today().isoformat() and pe is not None:
                merged.setdefault(stamp, {"date": stamp})["pe_ttm"] = pe
        capture(
            section,
            "recent",
            lambda: dataset(f"csi-valuation:{code}", lambda: ak.stock_zh_index_value_csindex(symbol=code)),
        )
        for row in section.get("recent", []):
            stamp = iso(row.get("日期"))
            if stamp and cutoff <= stamp <= date.today().isoformat():
                merged.setdefault(stamp, {"date": stamp}).update(
                    pe=number(row.get("市盈率1")), dividend_yield=number(row.get("股息率1"))
                )
        result["source"] = (
            "legulegu/csindex" if any(number(r.get("pb")) is not None for r in merged.values()) else "csindex"
        )
        result["pe_basis"] = "CSI PE1, total shares"
        result["historical_pe_basis"] = "CSI published rolling PE, not reconstructed from current constituents"
        result.setdefault("warnings", []).extend(section.get("warnings", []))
    result["metric_sources"] = {
        field: "csindex" if field == "pe_ttm" and "historical_pe_basis" in result else "legulegu"
        for field in ("pe_ttm", "pb")
        if any(number(r.get(field)) is not None for r in merged.values())
    }
    missing = [field for field in ("pe_ttm", "pb") if field not in result["metric_sources"]]
    if missing and os.environ.get("LIXINGER_TOKEN") and re.fullmatch(r"\d{6}|H\d{5}", code, re.I):
        section = {}
        capture(section, "rows", lambda: lixinger_index_history(code, cutoff, date.today().isoformat()))
        for row in section.get("rows", []):
            if row["date"] and cutoff <= row["date"] <= date.today().isoformat():
                for field in missing:
                    if row[field] is not None:
                        merged.setdefault(row["date"], {"date": row["date"]})[field] = row[field]
        for field in missing:
            if any(number(r.get(field)) is not None for r in merged.values()):
                result["metric_sources"][field] = "lixinger"
        result["lixinger_basis"] = "provider-defined total-market-cap-weighted (mcw) index valuation"
        result.setdefault("warnings", []).extend(section.get("warnings", []))
        if "lixinger" in result["metric_sources"].values():
            result["source"] = "/".join(sorted(set(result["metric_sources"].values())))
    if "pb" not in result["metric_sources"]:
        result.setdefault("warnings", []).append(
            "historical PB unavailable; optional LIXINGER_TOKEN enables an additional provider; no estimate substituted"
        )
    if not merged:
        raise ValueError("no index valuation data available")
    history = sorted(merged.values(), key=lambda r: r["date"])
    fields = ("pe_ttm", "pb", "pe", "dividend_yield") if "pe_basis" in result else ("pe_ttm", "pb")
    result.update(history=history, metrics={field: valuation_summary(history, field) for field in fields})
    if not result.get("warnings"):
        result.pop("warnings", None)
    return result


def hk_board_meetings(board):
    import requests

    url = f"https://www.hkex-is.hk/wwwroot/link/{'ebmn' if board == 'main' else 'ebmngem'}.htm"

    def fetch():
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        frames = pd.read_html(StringIO(response.text), header=0, converters={"Code": str}, keep_default_na=False)
        frame = next((f for f in frames if {"BM Date", "Code", "Purpose", "Period"}.issubset(f.columns)), None)
        if frame is None:
            raise ValueError("HKEX board-meeting table unavailable")
        rows = []
        for row in frame.to_dict("records"):
            code = str(row["Code"]).strip()
            purpose = str(row["Purpose"]).strip()
            if not re.fullmatch(r"\d{1,5}", code) or not re.search(r"\bRES(?:ULTS)?\b", purpose, re.I):
                continue
            stamp = iso(pd.to_datetime(str(row["BM Date"]).strip(), format="%d/%m/%Y", errors="coerce"))
            if not stamp:
                raise ValueError("invalid HKEX board-meeting date")
            period = str(row["Period"])
            match = re.search(r"\d{2}/\d{2}/\d{2}(?!\d)", period)
            report_date = iso(pd.to_datetime(match.group(), format="%d/%m/%y", errors="coerce")) if match else None
            rows.append(
                {
                    "symbol": code.zfill(5) + ".HK",
                    "scheduled_date": stamp,
                    "report_date": report_date,
                    "period_description": period,
                    "purpose": purpose,
                    "actual_date": None,
                    "confirmation": "official_board_meeting_notice",
                    "date_kind": "scheduled_board_meeting",
                    "source_url": url,
                }
            )
        return rows

    return cached(f"hk-board-meetings:{board}", DAY, fetch)


def earnings_calendar(args):
    import akshare as ak

    stamp = iso(args.report_date)
    suffix = {"03-31": "一季", "06-30": "半年报", "09-30": "三季", "12-31": "年报"}
    if not 1 <= args.top <= 500 or (args.report_date and not stamp):
        raise ValueError("report_date must be a valid date and top must be in 1..500")
    if args.symbol and detect_market(args.symbol) == "HK":
        import yfinance as yf

        _, hk_code = stock_identity(args.symbol)
        sections = {}
        for board in ("main", "gem"):
            capture(sections, board, lambda board=board: hk_board_meetings(board))
        events = [
            r
            for board in ("main", "gem")
            for r in sections.get(board, [])
            if r["symbol"] == hk_code + ".HK"
            and r["scheduled_date"] >= date.today().isoformat()
            and (not stamp or r["report_date"] == stamp)
        ]
        events = list({(r["scheduled_date"], r["purpose"], r["report_date"]): r for r in events}.values())
        events.sort(key=lambda r: r["scheduled_date"])

        def fetch():
            frame = yf.Ticker(_yf_hk_symbol(args.symbol)).get_earnings_dates(limit=12)
            if frame is None:
                raise ValueError("HK earnings schedule unavailable")
            return sorted({iso(d) for d in frame.index if iso(d)})

        if not events:
            capture(sections, "dates", lambda: cached(f"hk-earnings-schedule:{_yf_hk_symbol(args.symbol)}", DAY, fetch))
        provider_events = [
            {
                "symbol": args.symbol,
                "scheduled_date": d,
                "actual_date": None,
                "report_date": None,
                "confirmation": "provider_only",
                "date_kind": "provider_earnings_schedule",
            }
            for d in sections.get("dates", [])
            if d >= date.today().isoformat()
        ]
        if not stamp and not events:
            events = provider_events
        return {
            "symbol": args.symbol,
            "market": "HK",
            "source": "HKEX board-meeting notices"
            if any(r["confirmation"] == "official_board_meeting_notice" for r in events)
            else "yfinance earnings schedule",
            "report_date_requested": stamp,
            "disclosures": events[: args.top],
            "disclosures_total": len(events),
            "forecasts": [],
            "forecasts_total": 0,
            "unverified_schedules": provider_events[: args.top] if stamp else [],
            "warnings": sections.get("warnings", [])
            + [
                "official dates are scheduled board meetings approving results, not actual disclosure dates; notice list may not be exhaustive",
                "provider-only schedules have unverified fiscal periods and are not assigned to requested report_date",
            ],
            "as_of": date.today().isoformat(),
        }
    if not stamp or stamp[5:] not in suffix:
        raise ValueError("report_date must be a quarter end for A-share earnings calendars")
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


def northbound_holdings(symbol, report_date=""):
    import requests

    exchange = _cn_code(symbol)[:2]
    if exchange not in ("sh", "sz"):
        raise ValueError("Stock Connect holdings cover Shanghai/Shenzhen securities only")
    if report_date and (not iso(report_date) or iso(report_date)[5:] not in ("03-31", "06-30", "09-30", "12-31")):
        raise ValueError("report_date must be a quarter end")

    def fetch():
        if exchange == "sh":
            response = requests.get(
                "https://query.sse.com.cn/commonSoaQuery.do",
                params={
                    "sqlId": "FW_HGTZL_HGTSCSJ_HGTZQCYSL_L",
                    "tradeDate": report_date.replace("-", ""),
                    "secCodeName": symbol,
                    "isPagination": "true",
                    "pageHelp.pageSize": 20,
                    "pageHelp.pageNo": 1,
                },
                headers={"Referer": "https://www.sse.com.cn/"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("result", [])
            if payload.get("actionErrors") or payload.get("pageHelp", {}).get("pageCount", 0) > 1:
                raise ValueError("incomplete SSE holdings response")
            matches = [r for r in rows if str(r.get("secCode", "")).strip() == symbol]
            if len(matches) != 1:
                raise ValueError("official northbound holdings unavailable for this security/quarter")
            row = matches[0]
            stamp = iso(row.get("tradeDate"))
            shares = number(str(row.get("totalHoldings")).replace(",", ""))
            source = "SSE quarterly Stock Connect holdings"
        else:

            def query(stamp, code):
                response = requests.get(
                    "https://www.szse.cn/api/report/ShowReport/data",
                    params={
                        "SHOWTYPE": "JSON",
                        "CATALOGID": "SGT_SGTCGSL",
                        "txtDate": f"{stamp[:4]}Q{int(stamp[5:7]) // 3}",
                        "txtZqdm": code,
                    },
                    headers={"Referer": "https://www.szse.cn/"},
                    timeout=20,
                )
                response.raise_for_status()
                section = response.json()[0]
                if section.get("error"):
                    raise ValueError("SZSE holdings period unavailable")
                return section

            stamp = report_date
            if not stamp:
                end = date(date.today().year, ((date.today().month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
                for _ in range(2):
                    candidate = end.isoformat()
                    if query(candidate, "").get("data"):
                        stamp = candidate
                        break
                    end = date(end.year, end.month - 2, 1) - timedelta(days=1)
            if not stamp:
                raise ValueError("latest published SZSE holdings quarter unavailable")
            section = query(stamp, symbol)
            if section.get("metadata", {}).get("pagecount", 0) > 1:
                raise ValueError("incomplete SZSE holdings response")
            matches = [r for r in section.get("data", []) if str(r.get("zqdm", "")).strip() == symbol]
            if len(matches) != 1:
                raise ValueError("official northbound holdings unavailable for this security/quarter")
            shares = number(str(matches[0].get("cysl")).replace(",", ""))
            source = "SZSE quarterly Stock Connect holdings"
        if (
            not stamp
            or stamp > date.today().isoformat()
            or (report_date and stamp != report_date)
            or shares is None
            or shares < 0
        ):
            raise ValueError("invalid official holdings date or quantity")
        return {
            "as_of": stamp,
            "source": source,
            "shares": shares,
            "complete": True,
            "quantity_unit": "fund units" if normalize_stock_code(symbol)["is_etf"] else "shares",
            "note": "exchange-disclosed aggregate Stock Connect quantity for this security; not a top-ten shareholder inference",
        }

    return cached(f"northbound-official:{symbol}:{report_date or date.today().isoformat()}", DAY, fetch)


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
            "shares": sum(r["shares"] for r in nominee)
            if nominee and all(r["shares"] is not None for r in nominee)
            else None,
            "note": "HKSCC nominee disclosure; absence from top ten is not zero holdings. Daily northbound stock holding disclosure is no longer assumed.",
        }
        result["northbound_nominee"] = result["northbound"]
    capture(result, "northbound", lambda: northbound_holdings(symbol, args.report_date))
    if (
        not result["shareholder_counts"]
        and not result.get("top_float_shareholders")
        and not result.get("northbound", {}).get("complete")
    ):
        raise ValueError("shareholder sources unavailable")
    return result


def hk_dividend_ranking(report_date=""):
    import requests

    stamp = iso(report_date) if report_date else f"{date.today().year - 1}-12-31"
    if not stamp or stamp[5:] != "12-31":
        raise ValueError("HK dividend ranking requires an annual year-end report_date")

    def fetch():
        headers = {"Referer": "https://www.hkex.com.hk/", "User-Agent": "Mozilla/5.0"}
        response = requests.get(
            "https://www.hkex.com.hk/Listing/Sustainability/Shareholder-Value-in-Focus/Dividend-Yield-Ratio?sc_lang=en",
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        block = re.search(r"LabCI\.getToken\s*=\s*function\s*\(\)\s*\{(.*?)\}", response.text, re.S)
        token_match = re.search(r'return\s+"([^"]+)"', re.sub(r"//[^\n]*", "", block.group(1))) if block else None
        if not token_match:
            raise ValueError("HKEX public dividend feed unavailable")
        rows = []
        years = None
        total = None
        page = 1
        while total is None or len(rows) < total:
            try:
                response = requests.get(
                    "https://www1.hkex.com.hk/hkexwidget/data/getcapitalefficiencylist",
                    params={
                        "token": unquote(token_match.group(1)),
                        "type": "DYR",
                        "sector": "null",
                        "hsi": 0,
                        "page": page,
                        "limit": 3000,
                        "sort": 1,
                        "dir": 1,
                        "lang": "eng",
                        "callback": "stockrank",
                        "qid": 1,
                    },
                    headers=headers,
                    timeout=20,
                )
            except requests.RequestException:
                raise ValueError("HKEX dividend feed request failed") from None
            if response.status_code != 200:
                raise ValueError("HKEX dividend feed unavailable")
            match = re.fullmatch(r"\s*stockrank\((.*)\);?\s*", response.text, re.S)
            if not match:
                raise ValueError("unexpected HKEX dividend feed")
            data = json.loads(match.group(1))["data"]
            if (
                data.get("responsecode") != "000"
                or not isinstance(data.get("totalCount"), int)
                or not 0 < data["totalCount"] <= 100000
            ):
                raise ValueError("invalid HKEX dividend feed")
            if total is not None and (total != data["totalCount"] or years != data.get("year")):
                raise ValueError("HKEX dividend pages changed during fetch")
            total, years = data["totalCount"], data["year"]
            if not data["datalist"]:
                raise ValueError("incomplete HKEX dividend ranking")
            rows.extend(data["datalist"])
            page += 1
        if len(rows) != total or len({r["sym"] for r in rows}) != total or stamp[:4] not in years:
            raise ValueError("incomplete HKEX ranking or requested year unavailable")
        column = years.index(stamp[:4]) + 1
        items = []
        for row in rows:
            if not re.fullmatch(r"\d{1,5}", row["sym"]) or len(row["data"]) != len(years) + 1:
                raise ValueError("invalid HKEX dividend row")
            value = number(row["data"][column])
            if value is not None:
                items.append(
                    {
                        "symbol": row["sym"].zfill(5) + ".HK",
                        "name": row["nm"],
                        "dividend_yield": value,
                        "yield_year": stamp[:4],
                        "source_latest_period": row.get("period"),
                    }
                )
        return {
            "market": "HK",
            "report_date": stamp,
            "source": "HKEX/LSEG",
            "items": items,
            "yield_basis": "provider fiscal-year dividend yield ratio, percent; not current-price TTM cash yield",
        }

    return cached(f"hk-dividend-ranking:{stamp}", DAY, fetch)


def dividend_ranking(report_date="", market="A"):
    import akshare as ak

    if market == "HK":
        return hk_dividend_ranking(report_date)
    if market != "A":
        raise ValueError("dividend rankings cover A/HK only")
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
        result = dividend_ranking(args.report_date, getattr(args, "market", "A"))
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

        sources = {}
        capture(
            sources,
            "calendar",
            lambda: dataset(f"hk-dividend-calendar:{symbol}", lambda: ak.stock_hk_dividend_payout_em(symbol=symbol)),
        )
        calendar = [
            {
                "ex_date": iso(r.get("除净日")),
                "payment_date": iso(r.get("发放日")),
                "announcement_date": iso(r.get("最新公告日期")),
                "plan": r.get("分红方案"),
                "fiscal_year": r.get("财政年度"),
            }
            for r in sources.get("calendar", [])
        ]
        if args.mode == "calendar":
            if sources.get("warnings"):
                raise ValueError("HK dividend calendar unavailable")
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

        capture(sources, "cash", lambda: dataset(f"hk-dividend-cash:{symbol}", cash_history))
        capture(sources, "quote", lambda: ticker.info)
        paid = sources.get("cash", [])
        info = sources.get("quote") or {}
        if sources.get("warnings"):
            result["warnings"] = sources["warnings"]
            if not paid and not calendar:
                raise ValueError("HK dividend sources unavailable")
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
        [
            r
            for r in events
            if not (r.get("ex_date") or r.get("report_date")) or (r.get("ex_date") or r["report_date"]) >= cutoff
        ],
        key=lambda r: r.get("ex_date") or r.get("report_date") or "",
        reverse=True,
    )
    paid_events = [
        r
        for r in events
        if r["status"] in ("实施分配", "paid") and (not r.get("ex_date") or r["ex_date"] <= today.isoformat())
    ]
    incomplete_history = any(
        not r.get("ex_date") or r["cash_per_share"] is None or r["cash_per_share"] < 0 for r in paid_events
    )
    ttm_cutoff = (today - timedelta(days=365)).isoformat()
    incomplete_ttm = any(
        not r.get("ex_date") or (r["ex_date"] > ttm_cutoff and (r["cash_per_share"] is None or r["cash_per_share"] < 0))
        for r in paid_events
    )
    implemented = [
        r for r in paid_events if r.get("ex_date") and r["cash_per_share"] is not None and r["cash_per_share"] > 0
    ]
    ttm = [r for r in implemented if r["ex_date"] > ttm_cutoff]
    cash_by_currency = {}
    for row in ttm:
        currency = row.get("currency") or result.get("currency") or "unknown"
        cash_by_currency[currency] = round(cash_by_currency.get(currency, 0) + row["cash_per_share"], 6)
    currency_match = result.get("currency") and all(currency == result["currency"] for currency in cash_by_currency)
    cash = sum(r["cash_per_share"] for r in ttm) if currency_match and events and not incomplete_ttm else None
    if incomplete_history:
        result.setdefault("warnings", []).append(
            "paid dividend dates or cash amounts are missing/invalid; totals and streak are unknown where affected"
        )
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
        cash_per_share_ttm_by_currency=None if incomplete_ttm else cash_by_currency,
        dividend_yield_ttm=round(cash / price * 100, 4) if cash is not None and price and price > 0 else None,
        consecutive_payment_years=consecutive if events and not incomplete_history else None,
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


def etf_basket(symbol):
    import requests

    def fetch():
        if symbol.startswith("5"):
            response = requests.get(
                "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do",
                params={"fundCode": symbol},
                headers={"Referer": "https://www.sse.com.cn/"},
                timeout=20,
            )
            source = "SSE PCF"
        else:
            response = requests.get(
                "https://www.szse.cn/api/report/ShowReport/data",
                params={
                    "SHOWTYPE": "JSON",
                    "CATALOGID": "sgshqd",
                    "txtJCorDH": symbol,
                    "txtStart": (date.today() - timedelta(days=30)).isoformat(),
                    "txtEnd": date.today().isoformat(),
                },
                headers={"Referer": "https://www.szse.cn/"},
                timeout=20,
            )
            response.raise_for_status()
            dates = re.findall(rf"pcf_{re.escape(symbol)}_(\d{{8}})", json.dumps(response.json()))
            dates = [d for d in dates if iso(d) and iso(d) <= date.today().isoformat()]
            if not dates:
                raise ValueError("no published SZSE PCF found")
            response = requests.get(
                f"https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_{symbol}_{max(dates)}.xml", timeout=20
            )
            source = "SZSE PCF"
        response.raise_for_status()
        if b"<!DOCTYPE" in response.content.upper() or b"<!ENTITY" in response.content.upper():
            raise ValueError("unexpected PCF XML declarations")
        root = ElementTree.fromstring(response.content)
        for node in root.iter():
            node.tag = node.tag.split("}")[-1]
        if root.tag not in ("SSEPortfolioCompositionFile", "PCFFile"):
            raise ValueError("unexpected PCF document")
        fund_code = root.findtext("FundInstrumentID") or root.findtext("SecurityID")
        stamp = iso(root.findtext("TradingDay"))
        unit = number(root.findtext("CreationRedemptionUnit"))
        components = root.findall(".//Component")
        count = number(root.findtext("TotalRecordNum") or root.findtext("RecordNumber") or root.findtext("RecordNum"))
        if (
            fund_code != symbol
            or not stamp
            or stamp > date.today().isoformat()
            or not unit
            or unit <= 0
            or count != len(components)
            or not components
        ):
            raise ValueError("incomplete or mismatched PCF document")
        rows = []
        for node in components:
            code = node.findtext("InstrumentID") or node.findtext("UnderlyingSecurityID")
            quantity = number(node.findtext("Quantity") or node.findtext("ComponentShare"))
            if not code or quantity is None or quantity < 0:
                raise ValueError("invalid PCF component")
            rows.append(
                {
                    "code": code,
                    "name": node.findtext("InstrumentName") or node.findtext("UnderlyingSymbol"),
                    "quantity_per_unit": quantity,
                    "exchange_code": node.findtext("UnderlyingSecurityID")
                    if root.tag == "SSEPortfolioCompositionFile"
                    else node.findtext("UnderlyingSecurityIDSource"),
                    "cash_substitution_flag": node.findtext("SubstitutionFlag") or node.findtext("SubstituteFlag"),
                    "creation_premium_rate": number(
                        node.findtext("CreationPremiumRate") or node.findtext("PremiumRatio")
                    ),
                    "redemption_discount_rate": number(
                        node.findtext("RedemptionDiscountRate") or node.findtext("DiscountRatio")
                    ),
                    "substitution_cash_amount": number(node.findtext("SubstitutionCashAmount")),
                    "creation_cash_substitute": number(node.findtext("CreationCashSubstitute")),
                    "redemption_cash_substitute": number(node.findtext("RedemptionCashSubstitute")),
                }
            )
        return {
            "source": source,
            "as_of": stamp,
            "previous_trading_day": iso(root.findtext("PreTradingDay")),
            "creation_redemption_unit": unit,
            "previous_nav": {
                "as_of": iso(root.findtext("PreTradingDay")),
                "nav_per_unit": number(root.findtext("NAV")),
                "nav_per_creation_unit": number(root.findtext("NAVperCU")),
            },
            "previous_cash_component": number(root.findtext("PreCashComponent") or root.findtext("CashComponent")),
            "estimated_cash_component": number(
                root.findtext("EstimatedCashComponent") or root.findtext("EstimateCashComponent")
            ),
            "cash_unit": "CNY per creation/redemption unit",
            "max_cash_substitution_ratio": number(root.findtext("MaxCashRatio")),
            "creation_redemption_switch": root.findtext("CreationRedemptionSwitch"),
            "creation_flag": root.findtext("Creation"),
            "redemption_flag": root.findtext("Redemption"),
            "record_count": len(rows),
            "complete": True,
            "components": rows,
            "basis": "complete published creation/redemption basket, not actual fund holdings; quantities per creation unit, rates are fractions",
        }

    return cached(f"etf-basket:{symbol}:{date.today().isoformat()}", DAY, fetch)


def fund_disclosed_equities(symbol):
    import requests
    from akshare.utils import demjson
    from bs4 import BeautifulSoup

    limit = 10000

    def fetch(stamp):
        response = requests.get(
            "https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
            params={"type": "jjcc", "code": symbol, "topline": limit, "year": stamp[:4], "month": str(int(stamp[5:7]))},
            headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{symbol}.html"},
            timeout=20,
        )
        response.raise_for_status()
        text = response.text
        payload = demjson.decode(text[text.index("{") : text.rindex("}") + 1])
        soup = BeautifulSoup(payload["content"], "lxml")
        for block in soup.select("div.boxitem"):
            heading = block.find("h4")
            link = heading.find("a", href=True) if heading else None
            if not heading or not link or not link["href"].endswith(f"/{symbol}.html"):
                continue
            match = re.search(r"\d{4}-\d{2}-\d{2}", heading.get_text())
            if not match or match.group() != stamp:
                continue
            table = block.find("table")
            if table is None:
                raise ValueError("disclosed equity table unavailable")
            frame = pd.read_html(StringIO(str(table)), converters={"股票代码": str})[0]
            frame.columns = [re.sub(r"\s+", "", str(c)) for c in frame.columns]
            value_column = next((c for c in frame.columns if c in ("持仓市值（万元）", "持仓市值（万元人民币）")), None)
            if not {"股票代码", "股票名称", "占净值比例", "持股数（万股）"}.issubset(frame.columns) or not value_column:
                raise ValueError("unknown disclosed equity units/schema")
            if frame.empty or len(frame) >= limit:
                raise ValueError("empty or potentially truncated disclosed equity table")
            positions = []
            for row in frame.to_dict("records"):
                quantity = number(str(row["持股数（万股）"]).replace(",", ""))
                value = number(str(row[value_column]).replace(",", ""))
                positions.append(
                    {
                        "code": str(row["股票代码"]),
                        "name": str(row["股票名称"]) if pd.notna(row["股票名称"]) else None,
                        "weight_pct": number(str(row["占净值比例"]).replace("%", "")),
                        "quantity_shares": quantity * 10000 if quantity is not None else None,
                        "market_value_cny": value * 10000 if value is not None else None,
                    }
                )
            return {
                "source": "eastmoney periodic equity report",
                "as_of": stamp,
                "is_realtime": False,
                "position_count": len(positions),
                "equity_positions": positions,
                "coverage": "all received source-table equity rows, not total fund assets; source completeness is not independently verified",
                "source_url": f"https://fundf10.eastmoney.com/ccmx_{symbol}.html",
            }
        raise ValueError("requested semiannual/annual equity report not available")

    end = date(date.today().year, ((date.today().month - 1) // 6) * 6 + 1, 1) - timedelta(days=1)
    requested = end.isoformat()
    for _ in range(2):
        stamp = end.isoformat()
        try:
            result = cached(f"fund-equity-report:{symbol}:{stamp}", DAY, lambda stamp=stamp: fetch(stamp))
            return {**result, "requested_report_date": requested}
        except Exception:
            end = date(end.year, end.month - 5, 1) - timedelta(days=1)
    raise ValueError("latest disclosed semiannual/annual equity report unavailable")


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
    capture(result, "basket", lambda: etf_basket(symbol))
    if getattr(args, "include_full_holdings", False):
        capture(result, "disclosed_equity_report", lambda: fund_disclosed_equities(symbol))
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
    if (
        not nav
        and not sizes
        and not result.get("holdings")
        and not result.get("basket", {}).get("complete")
        and not result.get("disclosed_equity_report")
    ):
        raise ValueError("ETF detail sources unavailable")
    return result


ANNOUNCEMENT_CATEGORIES = {
    "all": ("", ""),
    "forecast": ("category_yjygjxz_szsh", "业绩预告"),
    "express": ("", "业绩快报"),
    "annual": ("category_ndbg_szsh", ""),
    "interim": ("category_bndbg_szsh", ""),
    "quarterly": ("category_yjdbg_szsh;category_sjdbg_szsh", ""),
    "reduction": ("category_gqbd_szsh", "减持"),
    "issuance": ("category_zf_szsh", ""),
    "inquiry": ("", "问询"),
    "dividend": ("category_qyfpxzcs_szsh", ""),
}


def announcements(args):
    import requests

    market, symbol = stock_identity(args.symbol)
    if market != "A" or not 1 <= args.top <= 500 or args.category not in ANNOUNCEMENT_CATEGORIES:
        raise ValueError("announcements require an A-share stock, valid category and top in 1..500")
    end = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start = date.fromisoformat(args.start_date) if args.start_date else end - timedelta(days=90)
    if start > end:
        raise ValueError("start_date must not exceed end_date")

    def stock_list():
        response = requests.get("https://www.cninfo.com.cn/new/data/szse_stock.json", timeout=20)
        response.raise_for_status()
        return response.json()["stockList"]

    stocks = cached("cninfo-stocks:A", DAY, stock_list)
    stock = next((r for r in stocks if r.get("code") == symbol), None)
    if not stock or not stock.get("orgId"):
        raise ValueError("stock not found in cninfo A-share directory")
    category, keyword = ANNOUNCEMENT_CATEGORIES[args.category]
    payload = {
        "pageNum": 1,
        "pageSize": 30,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{symbol},{stock['orgId']}",
        "searchkey": keyword,
        "category": category,
        "seDate": f"{start.isoformat()}~{end.isoformat()}",
        "sortName": "time",
        "sortType": "desc",
        "isHLtitle": "false",
    }
    rows, seen = [], set()
    total = 0
    # shortcut: bounded 17-page query, narrow the date range for more than 500 disclosures.
    for page in range(1, (args.top + 29) // 30 + 1):
        payload["pageNum"] = page
        response = requests.post(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            data=payload,
            headers={"Referer": "https://www.cninfo.com.cn/"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if "totalAnnouncement" not in data:
            raise ValueError("cninfo announcement response unavailable")
        total = int(data["totalAnnouncement"])
        items = data.get("announcements") or []
        for row in items:
            if row.get("secCode") != symbol:
                continue
            stamp = iso(pd.to_datetime(row.get("announcementTime"), unit="ms", utc=True))
            path = row.get("adjunctUrl") or ""
            if not stamp or not start.isoformat() <= stamp <= end.isoformat() or path in seen:
                continue
            if not re.fullmatch(r"finalpage/[\d-]+/[\w.-]+\.pdf", path, re.I):
                continue
            seen.add(path)
            title = unescape(re.sub(r"<[^>]+>", "", row.get("announcementTitle") or ""))
            inferred = next(
                (
                    key
                    for key, pattern in [
                        ("forecast", "业绩预告"),
                        ("express", "业绩快报"),
                        ("inquiry", "问询"),
                        ("reduction", "减持"),
                        ("interim", "半年度报告"),
                        ("annual", "年度报告"),
                        ("quarterly", "季度报告"),
                        ("issuance", "发行|增发|定增"),
                        ("dividend", "分红|权益分派|利润分配"),
                    ]
                    if re.search(pattern, title)
                ),
                "other",
            )
            rows.append(
                {
                    "title": title,
                    "date": stamp,
                    "category": inferred,
                    "pdf_url": f"https://static.cninfo.com.cn/{path}",
                }
            )
        if not items or page * 30 >= total or len(rows) >= args.top:
            break
    return {
        "symbol": args.symbol,
        "market": "A",
        "source": "cninfo",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total": total,
        "announcements": rows[: args.top],
        "truncated": total > len(rows[: args.top]),
        "body_extracted": False,
        "requested_category": args.category,
    }


def main():
    parser = argparse.ArgumentParser(description="Value-investing datasets and dated historical context")
    sub = parser.add_subparsers(dest="command")
    for command in ("valuation_history", "index_valuation"):
        p = sub.add_parser(command)
        p.add_argument("symbol")
        p.add_argument("--years", type=int, default=5)
    p = sub.add_parser("announcements")
    p.add_argument("symbol")
    p.add_argument("--category", choices=list(ANNOUNCEMENT_CATEGORIES), default="all")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--top", type=int, default=50)
    p = sub.add_parser("earnings_calendar")
    p.add_argument("--symbol", default="")
    p.add_argument("--report-date", default="")
    p.add_argument("--top", type=int, default=50)
    p = sub.add_parser("shareholder_structure")
    p.add_argument("symbol")
    p.add_argument("--report-date", default="")
    p = sub.add_parser("etf_details")
    p.add_argument("symbol")
    p.add_argument("--benchmark", default="")
    p.add_argument("--days", type=int, default=60)
    p.add_argument(
        "--include-full-holdings", action="store_true", help="Fetch latest disclosed semiannual/annual equity table"
    )
    p = sub.add_parser("dividends")
    p.add_argument("--symbol", default="")
    p.add_argument("--mode", choices=["history", "calendar", "ranking"], default="history")
    p.add_argument("--market", choices=["A", "HK"], default="A", help="Ranking market; history/calendar use symbol")
    p.add_argument("--report-date", default="")
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--top", type=int, default=20)
    try:
        args = parser.parse_args()
        handlers = {
            "announcements": announcements,
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
