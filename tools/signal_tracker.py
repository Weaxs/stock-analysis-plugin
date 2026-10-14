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
import contextlib
import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _subproc import command_timeout, run_tool, utf8_stdio  # noqa: E402
from stock_data import detect_market  # noqa: E402


def _default_store() -> Path:
    env = os.environ.get("STOCK_SIGNAL_STORE")
    return Path(env) if env else Path.home() / ".stock-analysis" / "signals.jsonl"


def _store_path(args) -> Path:
    return Path(args.store) if args.store else _default_store()


def _validate_signal(record):
    if not isinstance(record, dict) or any(
        not isinstance(record.get(field), str) or not record[field] for field in ("id", "symbol", "record_date")
    ):
        raise ValueError("Invalid signal identity/date")
    if record.get("status") not in ("open", "closed") or record.get("direction") not in ("buy", "sell"):
        raise ValueError("Invalid signal status/direction")
    if record.get("source") is not None and not isinstance(record["source"], str):
        raise ValueError("Signal source must be a string")
    if record.get("entry_price") is None or any(
        isinstance(record.get(field), bool)
        or not isinstance(record[field], (int, float))
        or not math.isfinite(record[field])
        or record[field] <= 0
        for field in ("entry_price", "target_price", "stop_price")
        if record.get(field) is not None
    ):
        raise ValueError("Signal prices must be finite and positive")
    horizon = record.get("horizon_days")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise ValueError("Signal horizon_days must be a positive integer")
    try:
        date = datetime.strptime(record["record_date"], "%Y-%m-%d")
    except ValueError as e:
        raise ValueError("Signal date must be YYYY-MM-DD") from e
    if date.strftime("%Y-%m-%d") != record["record_date"]:
        raise ValueError("Signal date must be YYYY-MM-DD")


@contextlib.contextmanager
def _store_lock(store: Path):
    # Lock a separate file: replacing the JSONL must not replace the lock inode.
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.with_name(store.name + ".lock").open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _load_records(store: Path, strict: bool = False) -> list:
    if not store.exists():
        return []
    records = []
    for number, line in enumerate(store.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if line:
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                if strict:
                    raise ValueError(f"Invalid signal JSON at line {number}; store left unchanged") from e
                continue
            if strict:
                try:
                    _validate_signal(record)
                except ValueError as e:
                    raise ValueError(f"Invalid signal record at line {number}; store left unchanged: {e}") from e
            records.append(record)
    return records


def _save_records(store: Path, records: list):
    """Atomic rewrite; caller holds the store lock."""
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=store.parent, delete=False) as stream:
            tmp = Path(stream.name)
            stream.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
        os.replace(tmp, store)
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)


def _append_record(store: Path, record: dict):
    """Append one complete JSON line; caller holds the store lock."""
    payload = (json.dumps(record, ensure_ascii=False, default=str, allow_nan=False) + "\n").encode("utf-8")
    with store.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END):
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":
                stream.write(b"\n")
        stream.write(payload)


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
        _validate_signal(record)
        with _store_lock(store):
            _load_records(store, strict=True)
            _append_record(store, record)
    except (OSError, ValueError) as e:
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


def _valid_bar(bar) -> bool:
    if not isinstance(bar, dict):
        return False
    try:
        date = datetime.strptime(bar["date"], "%Y-%m-%d")
    except (KeyError, TypeError, ValueError):
        return False
    return date.strftime("%Y-%m-%d") == bar["date"] and all(
        isinstance(bar.get(key), (int, float)) and math.isfinite(bar[key]) and bar[key] > 0
        for key in ("high", "low", "close")
    )


def _price_factor(rec, bars):
    if all(bar.get("adjusted") == "none" for bar in bars):
        return 1.0
    anchor = next((bar for bar in bars if bar["date"] == rec["record_date"]), {})
    basis = anchor.get("adjusted")
    factor = anchor.get("adjustment_factor")
    if (
        basis in ("qfq", "total_return")
        and all(bar.get("adjusted") == basis for bar in bars)
        and not isinstance(factor, bool)
        and isinstance(factor, (int, float))
        and math.isfinite(factor)
        and factor > 0
    ):
        return factor
    return None


def cmd_evaluate(args):
    deadline = time.monotonic() + command_timeout("signal_tracker.py", ["evaluate"]) - 30
    store = _store_path(args)
    try:
        with _store_lock(store):
            records = _load_records(store, strict=True)
    except (OSError, ValueError) as e:
        return {"error": str(e)}
    results = []
    evaluated = closed = 0
    changed = False
    deferred = 0
    for rec in records:
        if rec.get("status") != "open":
            continue
        if args.symbol and rec.get("symbol") != args.symbol:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            deferred += 1
            continue
        evaluated += 1
        entry = {"id": rec.get("id"), "symbol": rec.get("symbol")}
        bars = run_tool(
            "stock_data.py",
            ["kline", rec["symbol"], "--period", "daily", "--count", str(_kline_count(rec))],
            timeout=min(command_timeout("stock_data.py", ["kline"]), remaining),
        )
        if (
            not isinstance(bars, list)
            or not bars
            or any(not _valid_bar(bar) for bar in bars)
            or min(str(b.get("date", ""))[:10] for b in bars) > rec["record_date"]
        ):
            entry.update({"outcome": "skipped_no_data", "return_pct": None, "exit_date": None})
        elif (factor := _price_factor(rec, bars)) is None:
            entry.update(
                outcome="skipped_price_basis",
                return_pct=None,
                exit_date=None,
                warning="Adjusted or unknown prices lack a verified conversion to the original signal prices",
            )
        else:
            priced = dict(rec)
            for key in ("entry_price", "target_price", "stop_price"):
                if priced.get(key) is not None:
                    priced[key] *= factor
            ev = _evaluate_bars(priced, bars)
            if ev is None:
                entry.update({"outcome": "open", "return_pct": None, "exit_date": None})
            else:
                outcome, exit_price, exit_date = ev
                exit_price /= factor
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
                        "evaluation_price_basis": {"adjusted": bars[0].get("adjusted"), "factor": factor},
                    }
                )
                changed = True
                entry.update({"outcome": outcome, "return_pct": ret, "exit_date": exit_date})
        results.append(entry)
    if results:
        try:
            updates = {r["id"]: r for r in records if r.get("status") == "closed"}
            with _store_lock(store):
                current = _load_records(store, strict=True)
                merged = [updates.get(r["id"], r) if r.get("status") == "open" else r for r in current]
                if changed:
                    _save_records(store, merged)
                persisted = {r["id"]: r for r in merged}
                for entry in results:
                    rec = persisted[entry["id"]]
                    if rec.get("status") == "closed":
                        entry.update({key: rec.get(key) for key in ("outcome", "return_pct", "exit_date")})
                        closed += 1
        except (OSError, ValueError) as e:
            return {"error": str(e)}
    return {
        "evaluated": evaluated,
        "closed": closed,
        "still_open": evaluated + deferred - closed,
        "results": results,
        "deferred": deferred,
        "complete": deferred == 0,
        "warnings": ["Time budget exhausted; deferred signals remain open for the next evaluation"] if deferred else [],
    }


def cmd_summary(args):
    store = _store_path(args)
    try:
        with _store_lock(store):
            records = _load_records(store, strict=True)
    except (OSError, ValueError) as e:
        return {"error": str(e)}
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
    returns = [
        r["return_pct"]
        for r in closed_recs
        if isinstance(r.get("return_pct"), (int, float))
        and not isinstance(r["return_pct"], bool)
        and math.isfinite(r["return_pct"])
    ]
    by_source = {}
    for r in closed_recs:
        agg = by_source.setdefault(r.get("source") or "unknown", {"closed": 0, "wins": 0, "returns": []})
        agg["closed"] += 1
        value = r.get("return_pct")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            agg["returns"].append(value)
        if r.get("outcome") in ("target_hit", "timeout_profit"):
            agg["wins"] += 1
    for agg in by_source.values():
        samples = agg.pop("returns")
        agg["return_samples"] = len(samples)
        agg["avg_return_pct"] = round(sum(samples) / len(samples), 2) if samples else None
        agg["sample_warning"] = "Small observational sample; not strategy validation" if len(samples) < 15 else None
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
