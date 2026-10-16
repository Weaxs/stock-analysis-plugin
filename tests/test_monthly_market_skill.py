import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "bull-trend" / "scripts" / "market_trend.py"


@pytest.mark.parametrize("start", ["2025-03-03", "2025-3-3"])
def test_monthly_market_skill_runs_offline_with_explicit_prehistory(tmp_path, make_kline_data, start):
    prices = tmp_path / "prices.json"
    market = tmp_path / "market.json"
    bars = make_kline_data(360)
    for i, row in enumerate(bars):
        row.update(open=100 + i, close=100 + i, high=101 + i, low=99 + i)
        row["date"] = str(row["date"].date())
    prices.write_text(json.dumps(bars))
    market.write_text(json.dumps(bars))
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(prices),
            str(market),
            "--symbol",
            "AAPL",
            "--market-symbol",
            "SPY",
            "--start",
            start,
            "--allocation",
            "0.8704",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["model"] == "monthly_momentum_market_entry"
    assert result["market_symbol"] == "SPY"
    assert result["validation_status"] == "not_validated"
    assert len(result["trades"]) == 3
    assert {trade["date"] for trade in result["trades"]} == {"2025-03-03"}
    assert all(row["date"] >= "2025-03-03" for row in result["equity_curve"])
    assert result["warmup_start"] == "2024-01-01"


def test_monthly_market_skill_errors_are_json(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(empty),
            str(empty),
            "--symbol",
            "AAPL",
            "--market-symbol",
            "SPY",
            "--start",
            "2025-03-03",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    assert "error" in json.loads(run.stdout)
    assert "Traceback" not in run.stdout
