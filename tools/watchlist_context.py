#!/usr/bin/env python3
"""Watchlist context builder — condense watchlist analysis into agent-friendly summaries.

Not a report renderer. Outputs per-symbol score/trend/anomalies/risk + suggested next tools,
so the host agent can decide what to do next (write daily briefing, dig deeper, alert, etc.).
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_data import detect_market  # noqa: E402
from watchlist import analyze_watchlist  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent


def _find_python() -> str:
    venv = TOOLS_DIR.parent / ".venv" / "bin" / "python3"
    return str(venv) if venv.exists() else sys.executable


def _run_json(script: str, args: list[str], timeout: int = 30):
    cmd = [_find_python(), str(TOOLS_DIR / script)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def _trend_label(overall: str) -> str:
    return {
        "bullish": "bullish",
        "bearish": "bearish",
        "neutral": "neutral",
        "mixed": "mixed",
    }.get(overall, overall or "unknown")


def _suggest_next_tools(market: str, trend: str, anomalies: list, risk_level: str) -> list:
    tools = []
    high_anoms = [a for a in anomalies if a.get("severity") == "high"]

    if market == "A" and (trend == "bullish" or high_anoms):
        tools.append("get_capital_flow")
    if high_anoms:
        tools.append("search_comprehensive_intel")
    if risk_level in ("high", "medium"):
        tools.append("screen_risk")
    if trend == "bullish":
        tools.append("get_technical_analysis")
    if any(a.get("type", "").startswith("volume_") for a in anomalies):
        tools.append("get_volume_analysis")
    # dedupe, preserve order
    seen = set()
    return [t for t in tools if not (t in seen or seen.add(t))]


def _build_item(symbol: str, data: dict | None) -> dict:
    if not data:
        return {"symbol": symbol, "error": "no data"}

    tech = data.get("technical") or {}
    risk = data.get("risk") or {}

    # anomalies — separate call (watchlist gather doesn't include them)
    anom_result = _run_json("anomaly_detect.py", ["detect", symbol]) or {}
    anomalies = anom_result.get("anomalies", []) if isinstance(anom_result, dict) else []

    score = tech.get("signal_score")
    trend = _trend_label((tech.get("trend") or {}).get("overall", ""))
    risk_level = risk.get("risk_level", "unknown")
    veto = bool(risk.get("veto_buy", False))
    buy_signal = tech.get("buy_signal")
    market = detect_market(symbol)

    quote = data.get("quote") or {}

    return {
        "symbol": symbol,
        "market": market,
        "name": quote.get("name"),
        "price": quote.get("price"),
        "change_pct": quote.get("change_pct"),
        "score": score,
        "trend": trend,
        "buy_signal": buy_signal,
        "risk_level": risk_level,
        "veto_buy": veto,
        "anomalies": [
            {"type": a.get("type"), "severity": a.get("severity"), "direction": a.get("direction")} for a in anomalies
        ],
        "anomaly_count": len(anomalies),
        "next_tools": _suggest_next_tools(market, trend, anomalies, risk_level),
    }


def build_context(symbols: list[str], include_market_review: bool = False, workers: int = 3) -> dict:
    raw = analyze_watchlist(symbols, workers=workers)
    results = raw.get("results", {})

    items = []
    # fetch anomalies in parallel to keep it responsive
    with ThreadPoolExecutor(max_workers=min(workers * 2, 6)) as pool:
        futures = {pool.submit(_build_item, sym, results.get(sym)): sym for sym in symbols}
        for fut in as_completed(futures):
            items.append(fut.result())

    # preserve caller order
    order = {s: i for i, s in enumerate(symbols)}
    items.sort(key=lambda x: order.get(x["symbol"], 999))

    output = {
        "meta": {
            "provider": "watchlist_context",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "realtime_or_latest_trade_day",
            "fallback_used": False,
            "warnings": [],
        },
        "summary": {
            "total": len(symbols),
            "success": sum(1 for i in items if "error" not in i),
            "failed": sum(1 for i in items if "error" in i),
            "high_risk_count": sum(1 for i in items if i.get("risk_level") == "high"),
            "veto_count": sum(1 for i in items if i.get("veto_buy")),
        },
        "items": items,
    }

    if include_market_review:
        markets = sorted({i.get("market") for i in items if i.get("market")})
        review = {}
        for m in markets:
            r = _run_json("market_review.py", ["review", "--market", m], timeout=60)
            if r:
                review[m] = r
        output["market_review"] = review

    return output


def main():
    parser = argparse.ArgumentParser(description="Build watchlist context for host agent")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("build")
    p.add_argument("symbols", help="Comma-separated symbols")
    p.add_argument(
        "--include-market-review",
        action="store_true",
        help="Also fetch market review per involved market",
    )
    p.add_argument("--workers", type=int, default=3)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
    if not codes:
        print(json.dumps({"error": "No valid symbols"}))
        sys.exit(1)

    result = build_context(codes, include_market_review=args.include_market_review, workers=args.workers)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
