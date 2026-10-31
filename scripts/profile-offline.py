#!/usr/bin/env python3
"""Offline baseline: python scripts/profile-offline.py > baseline.json.

CLI --help includes interpreter/import/argparse cost, never provider latency.
Synthetic compute timings exclude imports; this is not a production throughput test.
"""

import cProfile
import json
import platform
import pstats
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def measure(operation, repeats=5):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        samples.append(round((time.perf_counter() - start) * 1000, 3))
    return {"median_ms": statistics.median(samples), "samples_ms": samples}


def main():
    cli = {}
    for path in sorted((ROOT / "tools").glob("*.py")):
        if '__name__ == "__main__"' not in path.read_text():
            continue

        def help_call(path=path):
            subprocess.run(
                [sys.executable, str(path), "--help"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=15,
                check=True,
            )

        cli[path.name] = measure(help_call, 3)

    import _cache
    import numpy as np
    import pandas as pd
    from backtest import load_strategy, simulate
    from stateless_analysis import portfolio
    from technical import analyze_records

    def bars(count):
        rng = np.random.default_rng(68)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, count)))
        return pd.DataFrame(
            {
                "date": pd.bdate_range("2000-01-03", periods=count).strftime("%Y-%m-%d"),
                "open": close,
                "close": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "volume": rng.integers(100000, 1000000, count),
            }
        )

    compute = {}
    strategy = load_strategy(str(ROOT / "strategies/examples/ma_crossover.yaml"))
    for count in (120, 2500):
        frame = bars(count)
        records = frame.to_dict("records")
        assert "error" not in analyze_records("TEST", records)
        compute[f"technical_{count}"] = measure(lambda records=records: analyze_records("TEST", records))
        compute[f"backtest_{count}"] = measure(lambda frame=frame: simulate(frame, strategy, 100000, "TEST"))
    for count in (10, 50, 100):
        histories = []
        for i in range(count):
            records = bars(250).to_dict("records")
            histories.append({"symbol": f"S{i}", "prices": [{"date": r["date"], "close": r["close"]} for r in records]})
        payload = {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": f"S{i}", "quantity": 1, "price": 100, "currency": "USD"} for i in range(count)],
            "histories": histories,
        }
        result = portfolio(payload)
        assert len(result["correlations"]) == count * (count - 1) // 2
        compute[f"portfolio_{count}_assets_250_dates"] = measure(lambda payload=payload: portfolio(payload), 3)
    profiler = cProfile.Profile()
    profiler.runcall(simulate, frame, strategy, 100000, "TEST")
    stats = pstats.Stats(profiler)
    hotspots = [
        {"function": f"{Path(file).name}:{line}:{name}", "calls": values[1], "cumulative_s": round(values[3], 4)}
        for (file, line, name), values in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:12]
    ]
    original_dir = _cache.CACHE_DIR
    with tempfile.TemporaryDirectory() as directory:
        try:
            _cache.CACHE_DIR = Path(directory)
            payload = [{"symbol": str(i), "value": i} for i in range(10000)]
            _cache.cached("profile", 60, lambda: payload)

            def unexpected_fetch():
                raise AssertionError("warm cache missed")

            compute["cache_read_10000_rows"] = measure(lambda: _cache.cached("profile", 60, unexpected_fetch))
        finally:
            _cache.CACHE_DIR = original_dir
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "cli": cli,
                "compute": compute,
                "backtest_hotspots": hotspots,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
