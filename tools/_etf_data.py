"""ETF tracking error, AUM scale, SSE/SZSE PCF basket, and disclosed equity helpers (extracted from value_data.py)."""

import json
import math
import re
from datetime import date, datetime, timedelta
from io import StringIO
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from _cache import DAY, cached
from _fund_data import fund_pingzhong_js


def _cn_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _number(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _iso(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else text


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


def fund_size_history(symbol, *, fetch_js=None, cache_fn=None):
    def fetch():
        match = re.search(r"\bData_fluctuationScale\s*=\s*(.*?);", (fetch_js or fund_pingzhong_js)(symbol), re.S)
        if not match:
            raise ValueError("fund scale history unavailable")
        data = json.loads(match.group(1))
        if len(data["categories"]) != len(data["series"]):
            raise ValueError("fund scale dates and observations do not match")
        return [
            {"date": _iso(stamp), "aum_cny": _number(row.get("y")) * 1e8}
            for stamp, row in zip(data["categories"], data["series"], strict=True)
            if _iso(stamp) and _number(row.get("y")) is not None
        ]

    return (cache_fn or cached)(f"etf-size:{symbol}", DAY, fetch)


def etf_basket(symbol, *, cache_fn=None, now_fn=None):
    import requests

    clock = now_fn or _cn_now

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
                    "txtStart": (clock().date() - timedelta(days=30)).isoformat(),
                    "txtEnd": clock().date().isoformat(),
                },
                headers={"Referer": "https://www.szse.cn/"},
                timeout=20,
            )
            response.raise_for_status()
            dates = re.findall(rf"pcf_{re.escape(symbol)}_(\d{{8}})", json.dumps(response.json()))
            dates = [d for d in dates if _iso(d) and _iso(d) <= clock().date().isoformat()]
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
        stamp = _iso(root.findtext("TradingDay"))
        unit = _number(root.findtext("CreationRedemptionUnit"))
        components = root.findall(".//Component")
        count = _number(root.findtext("TotalRecordNum") or root.findtext("RecordNumber") or root.findtext("RecordNum"))
        if (
            fund_code != symbol
            or not stamp
            or stamp > clock().date().isoformat()
            or not unit
            or unit <= 0
            or count != len(components)
            or not components
        ):
            raise ValueError("incomplete or mismatched PCF document")
        rows = []
        for node in components:
            code = node.findtext("InstrumentID") or node.findtext("UnderlyingSecurityID")
            quantity = _number(node.findtext("Quantity") or node.findtext("ComponentShare"))
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
                    "creation_premium_rate": _number(
                        node.findtext("CreationPremiumRate") or node.findtext("PremiumRatio")
                    ),
                    "redemption_discount_rate": _number(
                        node.findtext("RedemptionDiscountRate") or node.findtext("DiscountRatio")
                    ),
                    "substitution_cash_amount": _number(node.findtext("SubstitutionCashAmount")),
                    "creation_cash_substitute": _number(node.findtext("CreationCashSubstitute")),
                    "redemption_cash_substitute": _number(node.findtext("RedemptionCashSubstitute")),
                }
            )
        return {
            "source": source,
            "as_of": stamp,
            "previous_trading_day": _iso(root.findtext("PreTradingDay")),
            "creation_redemption_unit": unit,
            "previous_nav": {
                "as_of": _iso(root.findtext("PreTradingDay")),
                "nav_per_unit": _number(root.findtext("NAV")),
                "nav_per_creation_unit": _number(root.findtext("NAVperCU")),
            },
            "previous_cash_component": _number(root.findtext("PreCashComponent") or root.findtext("CashComponent")),
            "estimated_cash_component": _number(
                root.findtext("EstimatedCashComponent") or root.findtext("EstimateCashComponent")
            ),
            "cash_unit": "CNY per creation/redemption unit",
            "max_cash_substitution_ratio": _number(root.findtext("MaxCashRatio")),
            "creation_redemption_switch": root.findtext("CreationRedemptionSwitch"),
            "creation_flag": root.findtext("Creation"),
            "redemption_flag": root.findtext("Redemption"),
            "record_count": len(rows),
            "complete": True,
            "components": rows,
            "basis": "complete published creation/redemption basket, not actual fund holdings; quantities per creation unit, rates are fractions",
        }

    return (cache_fn or cached)(f"etf-basket:{symbol}", DAY, fetch)


def fund_disclosed_equities(symbol, *, cache_fn=None, now_fn=None):
    import requests

    clock = now_fn or _cn_now

    try:
        from akshare.utils.jsonish import loads as decode
    except ImportError:
        from akshare.utils.demjson import decode
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
        payload = decode(text[text.index("{") : text.rindex("}") + 1])
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
                quantity = _number(str(row["持股数（万股）"]).replace(",", ""))
                value = _number(str(row[value_column]).replace(",", ""))
                positions.append(
                    {
                        "code": str(row["股票代码"]),
                        "name": str(row["股票名称"]) if pd.notna(row["股票名称"]) else None,
                        "weight_pct": _number(str(row["占净值比例"]).replace("%", "")),
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

    end = date(clock().date().year, ((clock().date().month - 1) // 6) * 6 + 1, 1) - timedelta(days=1)
    requested = end.isoformat()
    for _ in range(2):
        stamp = end.isoformat()
        try:
            result = (cache_fn or cached)(f"fund-equity-report:{symbol}:{stamp}", DAY, lambda stamp=stamp: fetch(stamp))
            return {**result, "requested_report_date": requested}
        except Exception:
            end = date(end.year, end.month - 5, 1) - timedelta(days=1)
    raise ValueError("latest disclosed semiannual/annual equity report unavailable")
