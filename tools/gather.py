#!/usr/bin/env python3
"""Unified data gathering — parallel subprocess calls to tools for skill scripts."""

import argparse
import copy
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _subproc import command_timeout, json_safe, run_tool, utf8_stdio  # noqa: E402
from risk_screening import screen_risk  # noqa: E402
from stock_data import detect_market  # noqa: E402
from technical import analyze_records  # noqa: E402


def _run(script, args, timeout=None):
    return run_tool(script, args, timeout=timeout, parse_json=False, preserve_errors=True)


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


class Collection:
    """One invocation's exact CLI requests, shared across stock continuations."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.lock = threading.Lock()
        self.tasks = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.pool.shutdown(wait=True)

    def submit(self, script, args, timeout=None):
        key = (script, tuple(args), timeout if timeout is not None else command_timeout(script, args))
        with self.lock:
            if key not in self.tasks:

                def collect():
                    raw = _run(script, args, **({"timeout": timeout} if timeout is not None else {}))
                    return _parse_json(raw)

                self.tasks[key] = self.pool.submit(collect)
            return self.tasks[key]

    def json(self, script, args, timeout=None):
        return copy.deepcopy(self.submit(script, args, timeout).result())


def _run_all(tasks: dict, symbol: str, *, session=None, with_risk=False) -> dict:
    if session is None:
        with Collection() as session:
            return _run_all(tasks, symbol, session=session, with_risk=with_risk)
    for script, args in tasks.values():
        session.submit(script, args)
    records = session.json(*tasks["kline"])
    technical = analyze_records(symbol, records) if records is not None else None
    with ThreadPoolExecutor(max_workers=1) as continuations:
        risk = None
        if with_risk:
            inputs = {"quote": session.json(*tasks["quote"]), "technical": technical}

            def collect_risk():
                try:
                    return screen_risk(symbol, inputs=inputs, run=session.json)
                except Exception as exc:
                    return {"error": f"Risk analysis failed ({type(exc).__name__})"}

            risk = continuations.submit(collect_risk)
        result = {key: session.json(script, args) for key, (script, args) in tasks.items()}
        result["technical"] = technical
        if risk is not None:
            result["risk"] = risk.result()
    return result


def gather_analysis(symbol: str, *, session=None) -> dict:
    market = detect_market(symbol)
    tasks = {
        "quote": ("stock_data.py", ["quote", symbol]),
        "kline": ("stock_data.py", ["kline", symbol, "--period", "daily", "--count", "120"]),
        "financials": ("stock_data.py", ["financials", symbol]),
        "capital_flow": ("stock_data.py", ["capital_flow", symbol]),
        "chip_distribution": ("stock_data.py", ["chip_distribution", symbol]),
        "news": ("search_intel.py", ["search", _news_query(symbol)]),
        "social_sentiment": ("search_intel.py", ["sentiment", symbol]),
    }
    if market in ("A", "HK", "US"):
        tasks["regime"] = ("market_regime.py", ["detect", market])
    result = _run_all(tasks, symbol, session=session, with_risk=True)
    if market not in ("A", "HK", "US"):
        result["regime"] = {"error": f"Market regime unavailable for {market}"}
    return result


def gather_technical(symbol: str, kline_count: int = 120, with_quote: bool = False) -> dict:
    tasks = {
        "kline": ("stock_data.py", ["kline", symbol, "--period", "daily", "--count", str(kline_count)]),
    }
    if with_quote:
        tasks["quote"] = ("stock_data.py", ["quote", symbol])
    return _run_all(tasks, symbol)


def gather_screen(market: str = "A", top: int = 20, config: str | None = None) -> dict:
    args = ["screen", "--market", market, "--top", str(top)]
    if config:
        args += ["--config", config]
    raw = _run("screener.py", args, timeout=command_timeout("screener.py", args))
    return {"screen_results": _parse_json(raw)}


def gather_fundamental(symbol: str) -> dict:
    market = detect_market(symbol)
    tasks = {
        "quote": ("stock_data.py", ["quote", symbol]),
        "kline": ("stock_data.py", ["kline", symbol, "--period", "daily", "--count", "60"]),
        "financials": ("stock_data.py", ["financials", symbol]),
        "news": ("search_intel.py", ["search", _news_query(symbol)]),
        "stock_info": ("stock_data.py", ["stock_info", symbol]),
    }
    if market == "A":
        tasks["sector_rankings"] = ("stock_data.py", ["sector_rankings", "--top", "5", "--direction", "both"])
    result = _run_all(tasks, symbol)
    if market != "A":
        result["sector_rankings"] = {"error": f"Sector rankings unavailable for {market}"}
    return result


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
