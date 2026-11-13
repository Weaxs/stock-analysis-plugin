#!/usr/bin/env python3
"""Batch watchlist analysis — run gather_analysis for multiple stocks with concurrency control."""

import argparse
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _subproc import error_message, utf8_stdio
from gather import Collection, gather_analysis
from stock_data import _cn_code


def has_analysis_data(data) -> bool:
    if not isinstance(data, dict):
        return False
    for key, field in (("quote", "price"), ("kline", "close"), ("technical", "signal_score")):
        payload = data.get(key)
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict) or "error" in row:
                continue
            value = row.get(field)
            if isinstance(value, (int, float)) and math.isfinite(value) and (key == "technical" or value > 0):
                return True
    return False


def analyze_watchlist(symbols: list[str], workers: int = 3, *, session=None, on_result=None) -> dict:
    if isinstance(workers, bool) or not isinstance(workers, int) or workers <= 0:
        return {"error": "workers must be a positive integer"}
    results = {}
    success = 0
    failed = 0

    with Collection() if session is None else nullcontext(session) as session:

        def analyze(sym):
            data = gather_analysis(sym, session=session)
            if on_result is not None:
                on_result(sym, data)
            return data

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(analyze, sym): sym for sym in symbols}
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    data = future.result()
                    results[sym] = data
                    if has_analysis_data(data):
                        success += 1
                    else:
                        failed += 1
                except Exception:
                    results[sym] = None
                    failed += 1

    return {
        "results": results,
        "summary": {"total": len(symbols), "success": success, "failed": failed},
    }


def get_watchlist() -> dict:
    configured = os.environ.get("STOCK_WATCHLIST_FILE")
    data_root = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
    path = Path(configured) if configured else data_root / "stock-analysis" / "watchlist.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"symbols": [], "count": 0}

    values = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise ValueError("Watchlist file must contain a symbols array")
    symbols, keys = [], set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Watchlist symbols must be strings")
        upper = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,31}", upper):
            raise ValueError("Watchlist contains an invalid symbol")
        symbol = upper.lower() if re.fullmatch(r"(?:SH|SZ|BJ)[0-9]{6}", upper) else upper
        # Same key as dsh dashboardQuoteSymbol: 600519 and sh600519 are one stock.
        key = _cn_code(symbol) if re.fullmatch(r"[0-9]{6}", symbol) else symbol
        if key not in keys:
            keys.add(key)
            symbols.append(symbol)
    return {"symbols": symbols, "count": len(symbols)}


def main():
    parser = argparse.ArgumentParser(description="Batch watchlist analysis")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="Read the dsh dashboard watchlist")

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
    elif args.command == "list":
        try:
            result = get_watchlist()
        except (OSError, ValueError) as exc:
            result = {"error": error_message(exc)}
    else:
        parser.print_help()
        sys.exit(1)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    utf8_stdio()
    main()
