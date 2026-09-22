#!/usr/bin/env python3
"""Signal tracker — record/evaluate/summary for AI trade suggestions.

The single stateful exception in an otherwise stateless toolset: a small JSONL
store lets the host agent track the after-the-fact performance of the buy/sell
suggestions it issues. Store path: $STOCK_SIGNAL_STORE, else
~/.stock-analysis/signals.jsonl (pathlib.Path.home(), cross-platform). Every
subcommand accepts --store PATH to override (tests point it at tmp_path). The
host decides when to record and when to evaluate.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _subproc import run_tool, utf8_stdio  # noqa: E402
from stock_data import detect_market  # noqa: E402


def _default_store() -> Path:
    env = os.environ.get("STOCK_SIGNAL_STORE")
    return Path(env) if env else Path.home() / ".stock-analysis" / "signals.jsonl"


def _store_path(args) -> Path:
    return Path(args.store) if args.store else _default_store()


def _load_records(store: Path) -> list:
    if not store.exists():
        return []
    records = []
    for line in store.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _save_records(store: Path, records: list):
    """Atomic rewrite: write <store>.tmp, then os.replace onto the store."""
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.parent / (store.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    os.replace(tmp, store)


def cmd_record(args):
    entry_price = args.entry_price
    if entry_price is None:
        quote = run_tool("stock_data.py", ["quote", args.symbol])
        if not isinstance(quote, dict) or "error" in quote or quote.get("price") is None:
            return {"error": f"cannot determine entry price for {args.symbol}: quote unavailable"}
        entry_price = quote["price"]
    date = args.date or datetime.now().strftime("%Y-%m-%d")
    record = {
        "id": f"{date}-{args.symbol}-{uuid4().hex[:8]}",
        "symbol": args.symbol,
        "market": detect_market(args.symbol),
        "direction": args.direction,
        "entry_price": entry_price,
        "target_price": args.target_price,
        "stop_price": args.stop_price,
        "horizon_days": args.horizon_days,
        "source": args.source,
        "note": args.note,
        "record_date": date,
        "status": "open",
        "outcome": None,
    }
    store = _store_path(args)
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        return {"error": str(e)}
    return record


def _evaluate_bars(rec: dict, bars: list):
    """Judge one open record against daily bars after record_date.

    Returns (outcome, exit_price, exit_date), or None when the record stays open
    (fewer post-record bars than horizon_days and nothing triggered). Stop is
    checked before target within each bar — when both trigger on the same day
    the conservative stop_hit wins."""
    entry = rec.get("entry_price")
    if not isinstance(entry, (int, float)) or entry <= 0:
        return None
    direction = rec.get("direction")
    target = rec.get("target_price")
    stop = rec.get("stop_price")
    horizon = rec.get("horizon_days") or 10
    eligible = sorted(
        (b for b in bars if str(b.get("date", "")) > str(rec.get("record_date", ""))),
        key=lambda b: str(b.get("date", "")),
    )
    for i, bar in enumerate(eligible, 1):
        high, low, close = bar.get("high"), bar.get("low"), bar.get("close")
        if direction == "buy":
            hit_stop = stop is not None and low is not None and low <= stop
            hit_target = target is not None and high is not None and high >= target
        else:
            hit_stop = stop is not None and high is not None and high >= stop
            hit_target = target is not None and low is not None and low <= target
        if hit_stop:
            return "stop_hit", stop, bar.get("date")
        if hit_target:
            return "target_hit", target, bar.get("date")
        if i >= horizon:
            if not isinstance(close, (int, float)):
                return None
            ret = (close - entry) / entry * 100 * (1 if direction == "buy" else -1)
            return ("timeout_profit" if ret > 0 else "timeout_loss"), close, bar.get("date")
    return None


def _kline_count(rec: dict) -> int:
    """Daily bars to fetch for evaluation: horizon + buffer, but at least enough to
    reach back to record_date (calendar days >= trading days), capped at 800."""
    horizon = rec.get("horizon_days") or 10
    try:
        elapsed = (datetime.now() - datetime.strptime(str(rec.get("record_date", "")), "%Y-%m-%d")).days
    except ValueError:
        elapsed = 0
    return min(max(horizon + 40, elapsed + 15), 800)


def cmd_evaluate(args):
    store = _store_path(args)
    records = _load_records(store)
    results = []
    evaluated = closed = 0
    changed = False
    for rec in records:
        if rec.get("status") != "open":
            continue
        if args.symbol and rec.get("symbol") != args.symbol:
            continue
        evaluated += 1
        entry = {"id": rec.get("id"), "symbol": rec.get("symbol")}
        bars = run_tool(
            "stock_data.py",
            ["kline", rec["symbol"], "--period", "daily", "--count", str(_kline_count(rec))],
        )
        if not isinstance(bars, list):
            entry.update({"outcome": "skipped_no_data", "return_pct": None, "exit_date": None})
        else:
            ev = _evaluate_bars(rec, bars)
            if ev is None:
                entry.update({"outcome": "open", "return_pct": None, "exit_date": None})
            else:
                outcome, exit_price, exit_date = ev
                ret = round((exit_price - rec["entry_price"]) / rec["entry_price"] * 100, 2)
                if rec.get("direction") == "sell":
                    ret = -ret
                rec.update(
                    {
                        "status": "closed",
                        "outcome": outcome,
                        "exit_price": exit_price,
                        "exit_date": exit_date,
                        "return_pct": ret,
                    }
                )
                closed += 1
                changed = True
                entry.update({"outcome": outcome, "return_pct": ret, "exit_date": exit_date})
        results.append(entry)
    if changed:
        try:
            _save_records(store, records)
        except OSError as e:
            return {"error": str(e)}
    return {"evaluated": evaluated, "closed": closed, "still_open": evaluated - closed, "results": results}


def cmd_summary(args):
    records = _load_records(_store_path(args))
    filtered = records
    if args.source:
        filtered = [r for r in filtered if r.get("source") == args.source]
    if args.symbol:
        filtered = [r for r in filtered if r.get("symbol") == args.symbol]
    if args.status != "all":
        filtered = [r for r in filtered if r.get("status") == args.status]

    closed_recs = [r for r in filtered if r.get("status") == "closed"]
    closed = len(closed_recs)
    counts = {
        o: sum(1 for r in closed_recs if r.get("outcome") == o)
        for o in ("target_hit", "stop_hit", "timeout_profit", "timeout_loss")
    }
    wins = counts["target_hit"] + counts["timeout_profit"]
    returns = [r["return_pct"] for r in closed_recs if isinstance(r.get("return_pct"), (int, float))]
    by_source = {}
    for r in closed_recs:
        agg = by_source.setdefault(r.get("source") or "unknown", {"closed": 0, "wins": 0})
        agg["closed"] += 1
        if r.get("outcome") in ("target_hit", "timeout_profit"):
            agg["wins"] += 1
    for agg in by_source.values():
        agg["win_rate"] = round(agg["wins"] / agg["closed"] * 100, 2) if agg["closed"] else None
    return {
        "filters": {"source": args.source, "symbol": args.symbol, "status": args.status},
        "summary": {
            "total": len(filtered),
            "open": sum(1 for r in filtered if r.get("status") == "open"),
            "closed": closed,
            "target_hit": counts["target_hit"],
            "stop_hit": counts["stop_hit"],
            "timeout_profit": counts["timeout_profit"],
            "timeout_loss": counts["timeout_loss"],
            "win_rate": round(wins / closed * 100, 2) if closed else None,
            "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        },
        "by_source": by_source,
        "records": filtered,
    }


def main():
    parser = argparse.ArgumentParser(description="Signal tracker — AI suggestion outcome store (JSONL)")
    sub = parser.add_subparsers(dest="command")

    p_rec = sub.add_parser("record")
    p_rec.add_argument("symbol")
    p_rec.add_argument("--direction", required=True, choices=["buy", "sell"])
    p_rec.add_argument("--entry-price", type=float, default=None, help="Default: current price via stock_data quote")
    p_rec.add_argument("--target-price", type=float, default=None)
    p_rec.add_argument("--stop-price", type=float, default=None)
    p_rec.add_argument("--horizon-days", type=int, default=10)
    p_rec.add_argument("--source", default=None)
    p_rec.add_argument("--note", default=None)
    p_rec.add_argument("--date", default=None, help="Record date YYYY-MM-DD (default: today)")
    p_rec.add_argument("--store", default=None, help="JSONL store path override")

    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("--symbol", default=None)
    p_eval.add_argument("--store", default=None, help="JSONL store path override")

    p_sum = sub.add_parser("summary")
    p_sum.add_argument("--source", default=None)
    p_sum.add_argument("--symbol", default=None)
    p_sum.add_argument("--status", default="all", choices=["open", "closed", "all"])
    p_sum.add_argument("--store", default=None, help="JSONL store path override")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {"record": cmd_record, "evaluate": cmd_evaluate, "summary": cmd_summary}
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict) and "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    utf8_stdio()
    main()
