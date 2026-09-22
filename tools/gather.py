#!/usr/bin/env python3
"""Unified data gathering — parallel subprocess calls to tools for skill scripts."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _subproc import json_safe, run_tool, utf8_stdio  # noqa: E402
from stock_data import detect_market  # noqa: E402


# gather fans out the heavier CLIs, so the default timeout stays 60s; raw stdout
# wanted here (parsing is _parse_json's job); def keeps timeout kw-passable.
def _run(script, args, timeout=60):
    return run_tool(script, args, timeout=timeout, parse_json=False)


def _news_query(symbol: str) -> str:
    # CN sources rank poorly for an English query; US/HK sources for a Chinese one.
    return f"{symbol} 最新消息" if detect_market(symbol) == "A" else f"{symbol} stock news"


def _parse_json(raw: str | None):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _run_all(tasks: dict) -> dict:
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(_run, script, args): key for key, (script, args) in tasks.items()}
        return {key: _parse_json(f.result()) for f, key in futures.items()}


def gather_analysis(symbol: str) -> dict:
    tasks = {
        "quote": ("stock_data.py", ["quote", symbol]),
        "kline": ("stock_data.py", ["kline", symbol, "--period", "daily", "--count", "120"]),
        "technical": ("technical.py", ["analyze", symbol, "--period", "daily", "--count", "120"]),
        "financials": ("stock_data.py", ["financials", symbol]),
        "capital_flow": ("stock_data.py", ["capital_flow", symbol]),
        "news": ("search_intel.py", ["search", _news_query(symbol)]),
        "risk": ("risk_screening.py", ["screen", symbol]),
        "regime": ("market_regime.py", ["detect"]),
    }
    return _run_all(tasks)


def gather_technical(symbol: str, kline_count: int = 120, with_quote: bool = False) -> dict:
    tasks = {
        "kline": ("stock_data.py", ["kline", symbol, "--period", "daily", "--count", str(kline_count)]),
        "technical": ("technical.py", ["analyze", symbol, "--period", "daily", "--count", str(kline_count)]),
    }
    if with_quote:
        tasks["quote"] = ("stock_data.py", ["quote", symbol])
    return _run_all(tasks)


def gather_screen(market: str = "A", top: int = 20, config: str | None = None) -> dict:
    args = ["screen", "--market", market, "--top", str(top)]
    if config:
        args += ["--config", config]
    raw = _run("screener.py", args, timeout=120)
    return {"screen_results": _parse_json(raw)}


def gather_fundamental(symbol: str) -> dict:
    tasks = {
        "quote": ("stock_data.py", ["quote", symbol]),
        "kline": ("stock_data.py", ["kline", symbol, "--period", "daily", "--count", "60"]),
        "technical": ("technical.py", ["analyze", symbol, "--period", "daily", "--count", "60"]),
        "financials": ("stock_data.py", ["financials", symbol]),
        "news": ("search_intel.py", ["search", _news_query(symbol)]),
        "stock_info": ("stock_data.py", ["stock_info", symbol]),
        "sector_rankings": ("stock_data.py", ["sector_rankings", "--top", "5", "--direction", "both"]),
    }
    return _run_all(tasks)


def main():
    parser = argparse.ArgumentParser(description="Unified data gathering for skills")
    sub = parser.add_subparsers(dest="command")

    p_analysis = sub.add_parser("analysis")
    p_analysis.add_argument("symbol")

    p_technical = sub.add_parser("technical")
    p_technical.add_argument("symbol")
    p_technical.add_argument("--kline-count", type=int, default=120)
    p_technical.add_argument("--with-quote", action="store_true")

    p_screen = sub.add_parser("screen")
    p_screen.add_argument("--market", default="A")
    p_screen.add_argument("--top", type=int, default=20)
    p_screen.add_argument("--config")

    p_fundamental = sub.add_parser("fundamental")
    p_fundamental.add_argument("symbol")

    args = parser.parse_args()

    if args.command == "analysis":
        result = gather_analysis(args.symbol)
    elif args.command == "technical":
        result = gather_technical(args.symbol, args.kline_count, args.with_quote)
    elif args.command == "screen":
        result = gather_screen(args.market, args.top, args.config)
    elif args.command == "fundamental":
        result = gather_fundamental(args.symbol)
    else:
        parser.print_help()
        sys.exit(1)

    json.dump(json_safe(result), sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    utf8_stdio()
    main()
