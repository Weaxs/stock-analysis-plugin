"""Open-end fund NAV and TIAA investment-advisory portfolio helpers (extracted from stock_data.py)."""

import json
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from _subproc import error_message

_YOUZHIYOUXING_TIAA = {
    "TIAA001001": "长钱账户",
    "TIAA001002": "海外长钱",
    "TIAA001004": "稳钱账户",
}


def _parse_float(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _unix_quote_time(value) -> str | None:
    try:
        ts = float(value)
        if not math.isfinite(ts) or ts <= 0:
            return None
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _cn_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _clean_row(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v) for k, v in d.items()}


def _quote_tiaa(symbol: str, *, include_disclosures: bool = True) -> dict:
    import requests

    disclosure_name = _YOUZHIYOUXING_TIAA.get(symbol)
    result = {
        "symbol": symbol,
        "name": disclosure_name,
        "asset_type": "investment_advisory_portfolio",
        "type_description": "雪球投顾组合，无统一单位净值",
        "manager": None,
        "advisor_code": f"A{symbol[4:7]}",
        "advisor_org": None,
        "advisor_mapping_basis": "tiaa_prefix_best_effort",
        "advisor_source_url": None,
        "advisor_retrieved_at": None,
        "nav": None,
        "example_account_performance": None,
        "quote_type": None,
        "exchange": None,
        "source": None,
        "source_url": f"https://xueqiu.com/S/{symbol}",
        "data_as_of": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "official_disclosures": [],
        "public_search_results": [],
        "disclosure_provider": "youzhiyouxing" if disclosure_name else None,
        "disclosure_status": "unavailable" if disclosure_name else "unsupported_provider",
        "disclosures_partial": True,
        "disclosures_as_of": None,
        "note": (
            "投顾组合无统一净值；主理人及完整业绩/持仓需在对应投顾平台登录后查询；"
            "官方公开披露仅为部分历史信息，持有人收益随买入时点和调仓跟进不同"
        ),
    }
    try:
        from tiaa_disclosures import advisor_metadata

        result.update(advisor_metadata(symbol))
    except Exception:
        result.setdefault("warnings", []).append("advisor organization metadata unavailable")
    if not include_disclosures and disclosure_name:
        return result

    try:
        with requests.Session() as session:
            session.get(
                "https://xueqiu.com/",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
                allow_redirects=True,
            ).raise_for_status()
            response = session.get(
                "https://stock.xueqiu.com/v5/stock/quote.json",
                params={"symbol": symbol, "extend": "detail"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://xueqiu.com/"},
                timeout=10,
            )
            response.raise_for_status()
        payload = response.json()
        if payload.get("error_code") not in (None, 0):
            raise ValueError("xueqiu portfolio metadata unavailable")
        quote = (payload.get("data") or {}).get("quote") or {}
        if quote.get("symbol") != symbol:
            raise ValueError("xueqiu returned different portfolio metadata")
        result.update(
            name=quote.get("name") or disclosure_name,
            quote_type=quote.get("type"),
            exchange=quote.get("exchange"),
            source="xueqiu",
            data_as_of=_unix_quote_time(quote.get("timestamp")),
        )
    except Exception:
        result.setdefault("warnings", []).append(
            "xueqiu portfolio metadata unavailable; type guidance remains available"
        )
    if not include_disclosures:
        return result

    if disclosure_name and result["name"] in (None, disclosure_name):
        try:
            from tiaa_disclosures import public_disclosures

            disclosure = public_disclosures(symbol, disclosure_name)
            result["official_disclosures"] = disclosure["items"]
            result["disclosures_as_of"] = disclosure["as_of"]
            if disclosure["items"]:
                result["disclosure_status"] = "partial_public"
        except Exception:
            result.setdefault("warnings", []).append(
                "official public disclosures unavailable; type guidance remains available"
            )
    elif disclosure_name and result["name"]:
        result["disclosure_status"] = "unsupported_provider"
        result.setdefault("warnings", []).append("portfolio name does not match the registered disclosure source")
    else:
        from tiaa_disclosures import search_public

        result["public_search_results"] = search_public(symbol, result["name"])
        if result["public_search_results"]:
            result["disclosure_provider"] = "web_search"
            result["disclosure_status"] = "search_partial"
    return result


def _fund_js_value(text: str, name: str):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*", text)
    if not match:
        raise ValueError(f"eastmoney fund data missing {name}")
    try:
        return json.JSONDecoder().raw_decode(text[match.end() :])[0]
    except json.JSONDecodeError as exc:
        raise ValueError(f"eastmoney fund data has invalid {name}") from exc


def _fund_nav_date(stamp) -> str:
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def fund_pingzhong_js(symbol: str) -> str:
    """Eastmoney's per-fund JS bundle (NAV, scale, holdings series)."""
    import requests

    response = requests.get(f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js", timeout=15)
    response.raise_for_status()
    return response.text


def cmd_fund_quote(args, *, trend_count: int = 0, fetch_js=None):
    symbol = args.symbol
    if not re.fullmatch(r"\d{6}", symbol):
        return {"error": "open-end fund symbol must be exactly 6 digits"}
    try:
        text = (fetch_js or fund_pingzhong_js)(symbol)
        if _fund_js_value(text, "fS_code") != symbol:
            raise ValueError(f"eastmoney returned a different fund for {symbol}")
        name = _fund_js_value(text, "fS_name")
        nav_rows = _fund_js_value(text, "Data_netWorthTrend")
        accumulated_rows = [] if trend_count else _fund_js_value(text, "Data_ACWorthTrend")
        points = []
        for row in reversed(nav_rows):
            if not isinstance(row, dict):
                continue
            nav = _parse_float(row.get("y"))
            if nav is None or not math.isfinite(nav) or nav <= 0:
                continue
            try:
                day = _fund_nav_date(row.get("x"))
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            points.append((row, day, nav))
            if len(points) >= max(1, trend_count):
                break
        if not points:
            raise ValueError(f"eastmoney returned no NAV for {symbol}")
        latest, nav_date, nav = points[0]
        accumulated_by_date = {_fund_nav_date(row[0]): row[1] for row in accumulated_rows}
        today = _cn_now().date().isoformat()
        result = _clean_row(
            {
                "symbol": symbol,
                "name": name,
                "asset_type": "open_end_fund",
                "nav": nav,
                "accumulated_nav": accumulated_by_date.get(nav_date),
                "change_pct": latest.get("equityReturn"),
                "nav_date": nav_date,
                "data_as_of": nav_date,
                "as_of": nav_date,
                "as_of_basis": "provider",
                "stale": nav_date < today,
                "source": "eastmoney",
                "source_url": f"https://fund.eastmoney.com/{symbol}.html",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "note": (
                    "nav is published end-of-day NAV, not a real-time price; QDII disclosure may lag "
                    "1-2 trading days; no intraday estimated NAV is provided"
                ),
                # Eastmoney retired its per-fund intraday estimate (fundgz); its remaining estimate
                # page lists every fund at once, so the estimate keys stay but are always unavailable.
                "estimated_nav": None,
                "estimated_change_pct": None,
                "estimated_at": None,
                "estimate_status": "unavailable",
            }
        )
        if trend_count:
            result["trend"] = [{"date": day, "close": nav} for _, day, nav in reversed(points)]
        return result
    except Exception as e:
        return {"error": error_message(e)}
