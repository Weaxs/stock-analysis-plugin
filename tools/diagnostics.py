#!/usr/bin/env python3
"""Data source diagnostics — report which providers and services are usable per market.

Checks: (1) python package importable, (2) required env vars present.
By default does NOT hit the network — `reachable: null` (unknown) rather than guessed
(issue #25). Pass `--probe` to opt into lightweight bounded HTTP reachability probes.
"""

import argparse
import importlib
import importlib.util
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _subproc import error_message, json_safe, utf8_stdio  # noqa: E402
from capabilities import ALL_MARKETS  # noqa: E402
from sec_edgar import identity as _sec_identity  # noqa: E402

# provider -> (import_name, required_env_vars, markets, note)
PROVIDERS = {
    "akshare": ("akshare", [], ["A"], None),
    # tushare is consumed as a pure-HTTP API (requests.post to api.tushare.pro,
    # see stock_data._kline_tushare) — the tushare package is not in requirements,
    # so availability is the token, not a package probe
    "tushare": ("requests", ["TUSHARE_TOKEN"], ["A"], "pure-HTTP API (api.tushare.pro); no tushare package needed"),
    "lixinger": (
        "requests",
        ["LIXINGER_TOKEN"],
        ["A", "HK"],
        "optional index PB, CN/HK company valuation and statements; subscription permissions not probed",
    ),
    "efinance": ("efinance", [], ["A"], None),
    "tencent": ("requests", [], ["A", "HK", "US"], "qt.gtimg.cn quote/kline, no auth (issue #25)"),
    "sina": (
        "requests",
        [],
        ["A"],
        "hq.sinajs.cn quote + daily kline, no auth; full-market snapshot via akshare (issue #25)",
    ),
    "xueqiu": ("akshare", ["XUEQIU_TOKEN"], ["A"], "industry board fallback via akshare"),
    "baostock": ("baostock", [], ["A"], None),
    "yfinance": ("yfinance", [], ["HK", "US", "JP", "KR", "TW"], "quote may be delayed 15-20min"),
    "tradingview": ("requests", [], ["HK", "US"], "scanner.tradingview.com HK/US snapshots & HK sectors, no auth"),
    "finnhub": ("finnhub", ["FINNHUB_API_KEY"], ["HK", "US"], None),
    "longbridge": (
        "longport.openapi",
        ["LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN"],
        ["HK", "US"],
        None,
    ),
    "alphavantage": ("requests", ["ALPHAVANTAGE_API_KEY"], ["US"], None),
}

# Non-quote auxiliary services: search engines, filings, sentiment, credit/macro
# service -> (category, import_name, required_env_vars, note)
SERVICES = {
    "tavily": ("search", "requests", ["TAVILY_API_KEY"], "Tavily web search"),
    "brave": ("search", "requests", ["BRAVE_API_KEY"], "Brave web search"),
    "serpapi": ("search", "requests", ["SERPAPI_KEY"], "SerpAPI Google search"),
    "bocha": ("search", "requests", ["BOCHA_API_KEY"], "Bocha AI search"),
    "searxng": ("search", "requests", ["SEARXNG_BASE_URLS"], "Self-hosted SearXNG fallback (comma-separated URLs)"),
    "sec_edgar": ("filings", "requests", ["SEC_USER_AGENT"], "SEC EDGAR 13F / Form 4 / US DCF & valuation"),
    "cninfo": ("filings", "requests", [], "CNINFO A-share official announcements, no auth"),
    "hkexnews": ("filings", "requests", [], "HKEXnews HK announcements & board meetings, no auth"),
    "adanos_sentiment": (
        "sentiment",
        "requests",
        ["SENTIMENT_API_KEY"],
        "HK/US social sentiment (Reddit/X/Polymarket); URL via SENTIMENT_API_URL",
    ),
    "chinamoney_cds": ("credit", "requests", [], "CFETS-SHCH onshore CN CDS indices, no auth"),
    "dtcc_cds": ("credit", "requests", [], "DTCC public CDX/iTraxx daily files, no auth"),
    "fred_macro": ("macro", "requests", [], "FRED public CSV fallback for US Treasury yields, no auth"),
}

# Lightweight public probe endpoints (never send credentials or secrets in probe requests)
_PROBE_URLS = {
    "tencent": "https://qt.gtimg.cn/q=sh000001",
    "sina": "https://hq.sinajs.cn/list=sh000001",
    "tradingview": "https://scanner.tradingview.com/hongkong/scan",
    "yfinance": "https://query1.finance.yahoo.com/v1/test/getcrumb",
    "cninfo": "https://www.cninfo.com.cn/new/data/szse_stock.json",
    "hkexnews": "https://www1.hkexnews.hk",
    "sec_edgar": "https://data.sec.gov/submissions/",
    "chinamoney_cds": "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/bond/CDSIndex.json",
    "dtcc_cds": "https://kgc0418-tdw-data-0.s3.amazonaws.com/dashboard/Cumulative.json",
    "fred_macro": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
}


def _probe_url(name: str, timeout: float = 4.0) -> tuple[bool | None, int | None, str | None]:
    url = _PROBE_URLS.get(name)
    if not url:
        return None, None, None
    import requests

    headers = {
        "User-Agent": os.environ.get("SEC_USER_AGENT", "stock-analysis-plugin")
        if name == "sec_edgar"
        else "Mozilla/5.0"
    }
    if name == "sina":
        headers["Referer"] = "https://finance.sina.com.cn"
    t0 = time.monotonic()
    try:
        resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        # Some endpoints reject HEAD with 405/403 while accepting GET/POST — treat HTTP response < 500 as reachable.
        if resp.status_code == 405:
            resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
            resp.close()
        latency = round((time.monotonic() - t0) * 1000)
        ok = resp.status_code < 500
        return ok, latency, (None if ok else f"HTTP {resp.status_code}")
    except Exception as exc:
        return False, None, error_message(exc, 120)


def _check_entry(name: str, import_name: str, env_vars: list[str], extra: dict, note: str | None) -> dict:
    # find_spec avoids executing the package's top-level code — cheaper and
    # side-effect-free vs import_module. This is a capability probe, not a call.
    try:
        spec = importlib.util.find_spec(import_name)
        pkg_ok = spec is not None
        pkg_err = None if pkg_ok else f"package '{import_name}' not installed"
    except Exception as e:
        pkg_ok = False
        pkg_err = f"package '{import_name}' not installed: {e}"

    missing_env = [v for v in env_vars if not os.environ.get(v)]

    available = pkg_ok and not missing_env
    reasons = []
    if not pkg_ok:
        reasons.append(pkg_err)
    if missing_env:
        reasons.append(f"missing env: {', '.join(missing_env)}")

    entry = {
        "name": name,
        "available": available,
        "reachable": None,  # not probed unless --probe is explicitly passed
        **extra,
        "reason": "; ".join(reasons) if reasons else None,
    }
    if note:
        entry["note"] = note
    return entry


def _check_provider(name: str) -> dict:
    import_name, env_vars, markets, note = PROVIDERS[name]
    return _check_entry(name, import_name, env_vars, {"markets": markets}, note)


def _check_service(name: str) -> dict:
    category, import_name, env_vars, note = SERVICES[name]
    entry = _check_entry(name, import_name, env_vars, {"category": category}, note)
    if name == "sec_edgar" and entry["available"]:
        try:
            _sec_identity()
        except ValueError as exc:
            entry["available"] = False
            entry["reason"] = f"invalid SEC_USER_AGENT: {exc}"
    return entry


def _apply_probes(entries: list[dict]) -> None:
    targets = [e for e in entries if e["available"] and e["name"] in _PROBE_URLS]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=min(len(targets), 6)) as pool:
        futures = {pool.submit(_probe_url, e["name"]): e for e in targets}
        for future, entry in futures.items():
            reachable, latency_ms, err = future.result()
            entry["reachable"] = reachable
            if latency_ms is not None:
                entry["latency_ms"] = latency_ms
            if err:
                entry["probe_error"] = err


def diagnose(market: str = "all", probe: bool = False) -> dict:
    market = (market or "all").upper()
    all_providers = [_check_provider(p) for p in PROVIDERS]
    all_services = [_check_service(s) for s in SERVICES]
    if probe:
        _apply_probes(all_providers + all_services)

    markets_to_report = ALL_MARKETS if market == "ALL" else [market]

    search_ready = [s["name"] for s in all_services if s["category"] == "search" and s["available"]]
    top_warnings = []
    if not search_ready:
        top_warnings.append(
            "No search engine configured (TAVILY/BRAVE/SERPAPI/BOCHA/SEARXNG); news search tools unavailable"
        )
    if not any(s["name"] == "sec_edgar" and s["available"] for s in all_services):
        top_warnings.append("SEC_USER_AGENT not set — US 13F / Form 4 / SEC filings unavailable")

    result = {
        "meta": {
            "provider": "diagnostics",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "probed" if probe else "instant",
            "probed": bool(probe),
            "fallback_used": False,
            "warnings": top_warnings,
        },
        "markets": [],
        "services": all_services,
        "summary": {
            "search_engines_ready": search_ready,
            "sec_edgar_ready": any(s["name"] == "sec_edgar" and s["available"] for s in all_services),
            "sentiment_api_ready": any(s["name"] == "adanos_sentiment" and s["available"] for s in all_services),
        },
    }

    for m in markets_to_report:
        providers = [p for p in all_providers if m in p["markets"]]
        available = [p for p in providers if p["available"]]

        warnings = []
        if not available:
            warnings.append(f"no configured data source for {m} (package/credentials not ready)")
        # market-specific caveats
        if m == "A" and not any(p["name"] == "tushare" and p["available"] for p in providers):
            warnings.append("TUSHARE_TOKEN not set — Tushare unavailable; some deep A-share data may be limited")
        if (
            m in ("HK", "US")
            and not any(p["name"] == "longbridge" and p["available"] for p in providers)
            and not any(p["name"] == "finnhub" and p["available"] for p in providers)
        ):
            warnings.append(f"{m} quotes will be delayed (yfinance only)")
        if m in ("JP", "KR", "TW"):
            warnings.append(f"{m} is served by yfinance only — quotes delayed 15-20min, no failover")

        result["markets"].append(
            {
                "market": m,
                "available": bool(available),
                "providers": providers,
                "warnings": warnings,
            }
        )

    return result


def main():
    parser = argparse.ArgumentParser(description="Data source diagnostics")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("check")
    p.add_argument(
        "--market",
        default="ALL",
        type=str.upper,
        choices=ALL_MARKETS + ["ALL"],
        help="Market to diagnose (default: ALL)",
    )
    p.add_argument(
        "--probe",
        action="store_true",
        help="Opt into lightweight HTTP reachability probes for public endpoints",
    )
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = diagnose(args.market, probe=args.probe)
    json.dump(json_safe(result), sys.stdout, ensure_ascii=False, indent=2, default=str, allow_nan=False)
    print()


if __name__ == "__main__":
    utf8_stdio()
    main()
