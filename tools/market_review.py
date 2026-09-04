#!/usr/bin/env python3
"""Market review — daily market overview with temperature scoring."""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _run_tool(script: str, args: str, timeout: int = 30):
    cmd = [sys.executable, os.path.join(TOOLS_DIR, script)] + args.split()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def calc_temperature(stats: dict, indices: list) -> dict:
    """Calculate market temperature score from breadth, index change, and limit data."""
    scores = []
    weights = []

    if stats and "up_count" in stats and "down_count" in stats:
        up = stats["up_count"] or 0
        down = stats["down_count"] or 0
        total = up + down
        if total > 0:
            breadth = up / total * 100
            scores.append(breadth)
            weights.append(0.45)

    if indices and isinstance(indices, list):
        changes = [idx.get("change_pct") for idx in indices if idx.get("change_pct") is not None]
        if changes:
            avg_change = sum(changes) / len(changes)
            index_score = max(0, min(100, 50 + avg_change * 12))
            scores.append(index_score)
            weights.append(0.35)

    if stats:
        limit_up = stats.get("limit_up_count", 0) or 0
        limit_down = stats.get("limit_down_count", 0) or 0
        limit_total = limit_up + limit_down
        if limit_total > 0:
            limit_score = limit_up / limit_total * 100
            scores.append(limit_score)
            weights.append(0.20)

    if not scores:
        return {"score": 50, "level": "neutral", "signal": "yellow"}

    total_weight = sum(weights)
    temperature = sum(s * w for s, w in zip(scores, weights)) / total_weight

    if temperature >= 60:
        level, signal = "constructive", "green"
    elif temperature >= 40:
        level, signal = "neutral", "yellow"
    else:
        level, signal = "weak", "red"

    return {"score": round(temperature, 1), "level": level, "signal": signal}


def _signal_to_stance(signal: str) -> str:
    return {"green": "offensive", "yellow": "balanced", "red": "defensive"}.get(signal, "balanced")


def review_market(market: str = "A") -> dict:
    """Gather market data and compute review for a single market."""
    from datetime import datetime

    tasks = {}

    if market == "A":
        tasks["indices"] = ("stock_data.py", "market_indices --region cn")
        tasks["stats"] = ("stock_data.py", "market_stats --market A")
        tasks["sectors"] = ("stock_data.py", "sector_rankings --top 5 --direction both")
        tasks["news"] = ("search_intel.py", "search A股 今日 市场")
        tasks["regime"] = ("market_regime.py", "detect A")
    elif market == "HK":
        tasks["indices"] = ("stock_data.py", "market_indices --region hk")
        tasks["news"] = ("search_intel.py", "search 港股 今日 市场")
        tasks["regime"] = ("market_regime.py", "detect HK")
    elif market == "US":
        tasks["indices"] = ("stock_data.py", "market_indices --region us")
        tasks["news"] = ("search_intel.py", "search 美股 今日 市场")
        tasks["regime"] = ("market_regime.py", "detect US")
    else:
        return {"error": f"Unknown market: {market}"}

    results = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {key: executor.submit(_run_tool, script, args) for key, (script, args) in tasks.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=60)
            except Exception:
                results[key] = None

    temperature = calc_temperature(results.get("stats"), results.get("indices"))

    output = {
        "market": market,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "indices": results.get("indices"),
        "stats": results.get("stats"),
        "sectors": results.get("sectors"),
        "news": results.get("news"),
        "regime": results.get("regime"),
        "temperature": temperature,
        "strategy_stance": _signal_to_stance(temperature["signal"]),
    }
    return output


def review_all() -> list:
    """Review all markets."""
    results = []
    for m in ["A", "HK", "US"]:
        results.append(review_market(m))
    return results


def main():
    parser = argparse.ArgumentParser(description="Market review tool")
    sub = parser.add_subparsers(dest="command")

    p_review = sub.add_parser("review")
    p_review.add_argument("--market", default="A", choices=["A", "HK", "US", "all"])

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "review":
        result = review_all() if args.market == "all" else review_market(args.market)
        print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
