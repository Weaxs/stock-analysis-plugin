#!/usr/bin/env python3
"""Batch watchlist analysis — run gather_analysis for multiple stocks with concurrency control."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gather import gather_analysis


def analyze_watchlist(symbols: list[str], workers: int = 3) -> dict:
    results = {}
    success = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(gather_analysis, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                data = future.result()
                results[sym] = data
                success += 1
            except Exception:
                results[sym] = None
                failed += 1

    return {
        "results": results,
        "summary": {"total": len(symbols), "success": success, "failed": failed},
    }


def main():
    parser = argparse.ArgumentParser(description="Batch watchlist analysis")
    sub = parser.add_subparsers(dest="command")

    p_analyze = sub.add_parser("analyze")
    p_analyze.add_argument("symbols", help="Comma-separated stock codes")
    p_analyze.add_argument("--workers", type=int, default=3, help="Concurrency level (default 3)")

    args = parser.parse_args()

    if args.command == "analyze":
        codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
        if not codes:
            print(json.dumps({"error": "No valid symbols provided"}))
            sys.exit(1)
        result = analyze_watchlist(codes, workers=args.workers)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
