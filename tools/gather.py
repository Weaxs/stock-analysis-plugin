#!/usr/bin/env python3
"""Unified data gathering — parallel subprocess calls to tools for skill scripts."""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent


def _find_python() -> str:
    venv = TOOLS_DIR.parent / ".venv" / "bin" / "python3"
    if venv.exists():
        return str(venv)
    return sys.executable


def _run(script: str, args: list[str], timeout: int = 60) -> str | None:
    python = _find_python()
    cmd = [python, str(TOOLS_DIR / script)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return None


def _parse_json(raw: str | None):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def gather_analysis(symbol: str) -> dict:
    tasks = {
        "quote": ("stock_data.py", ["quote", symbol]),
        "kline": ("stock_data.py", ["kline", symbol, "--period", "daily", "--count", "120"]),
        "technical": ("technical.py", ["analyze", symbol, "--period", "daily", "--count", "120"]),
        "financials": ("stock_data.py", ["financials", symbol]),
        "capital_flow": ("stock_data.py", ["capital_flow", symbol]),
        "news": ("search_intel.py", ["search", f"{symbol} stock news"]),
        "risk": ("risk_screening.py", ["screen", symbol]),
        "regime": ("market_regime.py", ["detect"]),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_run, script, args): key for key, (script, args) in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = _parse_json(future.result())

    return results


def gather_technical(symbol: str, kline_count: int = 120, with_quote: bool = False) -> dict:
    tasks = {
        "kline": ("stock_data.py", ["kline", symbol, "--period", "daily", "--count", str(kline_count)]),
        "technical": ("technical.py", ["analyze", symbol, "--period", "daily", "--count", str(kline_count)]),
    }
    if with_quote:
        tasks["quote"] = ("stock_data.py", ["quote", symbol])

    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_run, script, args): key for key, (script, args) in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = _parse_json(future.result())

    return results


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
        "news": ("search_intel.py", ["search", f"{symbol} stock news"]),
        "stock_info": ("stock_data.py", ["stock_info", symbol]),
        "sector_rankings": ("stock_data.py", ["sector_rankings", "--top", "5", "--direction", "both"]),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(_run, script, args): key for key, (script, args) in tasks.items()}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = _parse_json(future.result())

    return results


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

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
