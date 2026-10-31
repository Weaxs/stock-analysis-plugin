#!/usr/bin/env python3
"""Offline workflow baseline with counted, fixed-latency CLI substitutes.

Run with the repo venv (pytest/pandas/numpy installed):
  python scripts/profile-workflows.py [checkout-root]
This measures orchestration only, not provider throughput. Reuses the test OHLCV fixture.
"""

import importlib.util
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "tools"))
import anomaly_detect  # noqa: E402
import gather  # noqa: E402
import risk_screening  # noqa: E402
import technical  # noqa: E402
import watchlist_context  # noqa: E402

spec = importlib.util.spec_from_file_location("perf_fixtures", root / "tests/conftest.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
bars = fixtures.make_kline_data.__wrapped__()(120, "volatile")
for row in bars:
    row["date"] = row["date"].strftime("%Y-%m-%d")
calls = Counter()
lock = threading.Lock()
active = peak = 0


def invoke(script, args, **kwargs):
    global active, peak
    if script == "risk_screening.py" and args[0] == "screen":
        return risk_screening.screen_risk(args[1])
    if script == "technical.py":
        rows = invoke("stock_data.py", ["kline", args[1], "--count", "120"])
        return technical.analyze_records(args[1], rows)
    if script == "anomaly_detect.py":
        return anomaly_detect.detect_anomalies(args[1])
    key = script + ":" + args[0]
    with lock:
        calls[key] += 1
        active += 1
        peak = max(peak, active)
    time.sleep(0.01)
    with lock:
        active -= 1
    if args[0] == "kline":
        n = int(args[args.index("--count") + 1]) if "--count" in args else 120
        return bars[-n:]
    if args[0] == "quote":
        return {"price": 10, "pe": 15, "pb": 2}
    if args[0] == "lockup":
        return {"upcoming_30d": [], "flags": [], "available": True}
    if args[0] == "capital_flow":
        return []
    if script == "market_regime.py":
        return {"market": args[1]}
    return []


gather._run = lambda script, args, **kw: json.dumps(invoke(script, args, **kw))
risk_screening._run_tool = invoke
anomaly_detect._run_tool = invoke
anomaly_detect.fetch_kline = lambda symbol, period, count: invoke(
    "stock_data.py", ["kline", symbol, "--count", str(count)]
)
watchlist_context._run_json = invoke
start = time.perf_counter()
result = watchlist_context.build_context(["600519", "000001", "000002"], workers=3)
assert result["summary"]["success"] == 3
print(
    json.dumps(
        {
            "seconds": time.perf_counter() - start,
            "calls": dict(calls),
            "total_calls": sum(calls.values()),
            "peak_leaf_calls": peak,
            "summary": result["summary"],
        }
    )
)
