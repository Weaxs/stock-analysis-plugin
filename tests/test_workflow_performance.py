"""Full workflow requests, snapshots and bounded leaf concurrency (offline)."""

import json
import threading
import time
from collections import Counter

import pytest

from tools import watchlist_context


@pytest.mark.parametrize("include_review", [False, True])
def test_context_reuses_snapshot_and_market_with_bounded_collection(monkeypatch, make_kline_data, include_review):
    import anomaly_detect
    import gather
    import market_review
    import risk_screening
    import technical

    bars = make_kline_data(120, "volatile")
    for row in bars:
        row["date"] = row["date"].strftime("%Y-%m-%d")
    calls = Counter()
    lock = threading.Lock()
    active = peak = 0

    def collect(script, args, timeout=None, **kwargs):
        nonlocal active, peak
        if script == "risk_screening.py" and args[0] == "screen":
            return risk_screening.screen_risk(args[1])
        if script == "market_review.py":
            return market_review.review_market(args[-1])
        if script == "technical.py":
            return technical.analyze_records(args[1], collect("stock_data.py", ["kline", args[1]]))
        if script == "anomaly_detect.py":
            return anomaly_detect.detect_anomalies(args[1])
        with lock:
            calls[script, args[0]] += 1
            active += 1
            peak = max(peak, active)
        time.sleep(0.005)
        with lock:
            active -= 1
        if args[0] == "kline":
            count = int(args[args.index("--count") + 1]) if "--count" in args else 120
            return bars[-count:]
        if args[0] == "quote":
            return {"price": 10, "pe": 15, "pb": 2}
        if args[0] == "lockup":
            return {"upcoming_30d": [], "flags": [], "available": True}
        if script == "market_regime.py":
            return {"market": args[1]}
        return []

    monkeypatch.setattr(gather, "_run", lambda script, args, **kw: json.dumps(collect(script, args, **kw)))
    monkeypatch.setattr(risk_screening, "_run_tool", collect)
    monkeypatch.setattr(market_review, "_run_tool", collect)
    monkeypatch.setattr(anomaly_detect, "_run_tool", collect)
    monkeypatch.setattr(
        anomaly_detect,
        "fetch_kline",
        lambda symbol, period, count: collect("stock_data.py", ["kline", symbol, "--count", str(count)]),
    )
    monkeypatch.setattr(watchlist_context, "_run_json", collect)
    result = watchlist_context.build_context(
        ["600519", "000001", "000002"], workers=3, include_market_review=include_review
    )
    assert calls["stock_data.py", "quote"] == 3
    assert calls["stock_data.py", "kline"] == 3
    assert calls["market_regime.py", "detect"] == 1
    assert peak <= 6
    assert [item["symbol"] for item in result["items"]] == ["600519", "000001", "000002"]
    assert result["summary"]["success"] == 3
    assert all(item["price"] == 10 for item in result["items"])
    first_counts = calls.copy()
    watchlist_context.build_context(["600519", "000001", "000002"], workers=3, include_market_review=include_review)
    assert calls == Counter({key: count * 2 for key, count in first_counts.items()})


def test_context_continues_ready_stock_before_slow_stock_finishes(monkeypatch):
    import watchlist

    fast_anomaly = threading.Event()

    def gather(symbol, **kwargs):
        if symbol == "SLOW":
            assert fast_anomaly.wait(2), "ready stock was held behind the full watchlist"
        return {"quote": {"price": 10}, "kline": []}

    def anomalies(symbol, **kwargs):
        if symbol == "FAST":
            fast_anomaly.set()
        return {"anomalies": []}

    monkeypatch.setattr(watchlist, "gather_analysis", gather)
    monkeypatch.setattr(watchlist_context, "detect_anomalies", anomalies)
    result = watchlist_context.build_context(["SLOW", "FAST"], workers=2)
    assert fast_anomaly.is_set()
    assert result["summary"]["success"] == 2
    assert [row["symbol"] for row in result["items"]] == ["SLOW", "FAST"]


def test_context_keeps_quote_when_reused_anomaly_input_is_invalid(monkeypatch):
    monkeypatch.setattr(
        watchlist_context,
        "analyze_watchlist",
        lambda *a, **kw: {"results": {"AAPL": {"quote": {"price": 10}, "kline": [{"bad": "bar"}] * 60}}},
    )
    result = watchlist_context.build_context(["AAPL"])
    assert result["summary"]["partial"] == 1
    assert result["items"][0]["price"] == 10
    assert result["items"][0]["anomaly_status"] == "unavailable"


def test_independent_risk_collection_starts_while_bars_are_pending(monkeypatch):
    from tools import gather

    lockup_started = threading.Event()
    observed = []

    def collect(script, args, **kwargs):
        if args[0] == "kline":
            observed.append(lockup_started.wait(1))
            return "[]"
        if args[0] == "lockup":
            lockup_started.set()
            return json.dumps({"upcoming_30d": [], "flags": [], "available": True})
        return "{}"

    monkeypatch.setattr(gather, "_run", collect)
    gather.gather_analysis("AAPL")
    assert observed == [True]
