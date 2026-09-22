#!/usr/bin/env python3
"""Market review — daily market overview with temperature scoring.

Review archive: `render_market_report --save` appends the report object to a
JSONL store so `history` can answer longitudinal questions. Store path:
$STOCK_REVIEW_STORE, else ~/.stock-analysis/reviews.jsonl (pathlib.Path.home(),
cross-platform). `history` accepts --store PATH to override (tests point it at
tmp_path).
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _subproc import run_tool as _run_tool
from _subproc import utf8_stdio
from signal_tracker import _load_records


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
        tasks["indices"] = ("stock_data.py", ["market_indices", "--region", "cn"])
        tasks["stats"] = ("stock_data.py", ["market_stats"])
        tasks["sectors"] = ("stock_data.py", ["sector_rankings", "--top", "5", "--direction", "both"])
        # query 含空格，必须保持单个 argv 元素
        tasks["news"] = ("search_intel.py", ["search", "A股 今日 市场"])
        tasks["regime"] = ("market_regime.py", ["detect", "A"])
    elif market == "HK":
        tasks["indices"] = ("stock_data.py", ["market_indices", "--region", "hk"])
        tasks["news"] = ("search_intel.py", ["search", "港股 今日 市场"])
        tasks["regime"] = ("market_regime.py", ["detect", "HK"])
    elif market == "US":
        tasks["indices"] = ("stock_data.py", ["market_indices", "--region", "us"])
        tasks["news"] = ("search_intel.py", ["search", "美股 今日 市场"])
        tasks["regime"] = ("market_regime.py", ["detect", "US"])
    else:
        return {"error": f"Unknown market: {market}"}

    results = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            # stats fans out to a full-market snapshot — screener gives the same path 240s
            # (screener.fetch_snapshot); the default 30s would silently degrade the
            # temperature to the neutral 50 fallback on a weak network.
            key: executor.submit(_run_tool, script, args, 240 if key == "stats" else 30)
            for key, (script, args) in tasks.items()
        }
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=300)
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


def _default_store() -> Path:
    env = os.environ.get("STOCK_REVIEW_STORE")
    return Path(env) if env else Path.home() / ".stock-analysis" / "reviews.jsonl"


def save_review(report: dict, store: Path | None = None) -> dict:
    """Append one review report object to the JSONL archive (side effect of render --save)."""
    store = store or _default_store()
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        return {"error": str(e)}
    return {"saved": True, "date": report.get("review_date") or report.get("date")}


def cmd_history(args) -> dict:
    """Recent archived reviews, newest first. Empty store -> clean empty list."""
    try:
        records = _load_records(Path(args.store) if args.store else _default_store())
    except (OSError, UnicodeDecodeError) as e:
        # store is a directory / not UTF-8 — same clean-error contract as save_review
        return {"error": str(e)}
    limit = max(args.limit, 0)
    recent = records[-limit:][::-1] if limit else []
    return {"count": len(recent), "records": recent}


def main():
    parser = argparse.ArgumentParser(description="Market review tool")
    sub = parser.add_subparsers(dest="command")

    p_review = sub.add_parser("review")
    p_review.add_argument("--market", default="A", choices=["A", "HK", "US", "all"])

    p_hist = sub.add_parser("history", help="Read archived market reviews (newest first)")
    p_hist.add_argument("--limit", type=int, default=10, help="Max archived entries to return (default 10)")
    p_hist.add_argument("--store", default=None, help="JSONL store path override")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "review":
        result = review_all() if args.market == "all" else review_market(args.market)
    else:  # history
        result = cmd_history(args)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    utf8_stdio()
    main()
