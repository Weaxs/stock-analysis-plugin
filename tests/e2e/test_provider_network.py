"""Layer 2 e2e: real provider network calls.

Marked @pytest.mark.integration_network — main test job skips these.
Run explicitly: `pytest -m integration_network`.

Failures here indicate:
  - upstream provider (akshare/yfinance) down or rate-limiting
  - schema drift in provider response
  - our fallback chain broken
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"


pytestmark = pytest.mark.integration_network


def _run_cli(script: str, args: list[str], timeout: int = 60) -> dict | list:
    cmd = [sys.executable, str(TOOLS_DIR / script)] + args
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    assert r.returncode == 0, f"exit {r.returncode}\nstderr: {r.stderr[:500]}"
    return json.loads(r.stdout)


class TestAShareData:
    """Real akshare calls. Fails hard if akshare or its upstream is down."""

    def test_market_indices_cn(self):
        out = _run_cli("stock_data.py", ["market_indices", "--region", "cn"])
        assert isinstance(out, list) and len(out) > 0, "expected list of indices"
        # every index should have name + price
        for idx in out:
            assert "name" in idx or "代码" in idx, f"missing name field: {idx}"

    def test_kline_a_share(self):
        out = _run_cli(
            "stock_data.py",
            ["kline", "600519", "--period", "daily", "--count", "10"],
        )
        assert isinstance(out, list) and len(out) > 0, "expected kline rows"
        first = out[0]
        for field in ("date", "open", "high", "low", "close", "volume"):
            assert field in first, f"kline missing {field}"


class TestUSStockData:
    """Real yfinance calls."""

    def test_kline_us(self):
        out = _run_cli(
            "stock_data.py",
            ["kline", "AAPL", "--period", "daily", "--count", "10"],
        )
        assert isinstance(out, list) and len(out) > 0, "expected AAPL kline rows"
        first = out[0]
        for field in ("date", "open", "close"):
            assert field in first, f"kline missing {field}"


class TestTradingCalendar:
    """exchange-calendars is a local library — not network per se, but exercises the same path."""

    def test_check_cn_trading_day(self):
        out = _run_cli("trading_calendar.py", ["check", "CN", "--date", "2026-06-30"])
        # 2026-06-30 is a Tuesday
        assert "is_trading_day" in out or "market" in out, f"unexpected shape: {out}"
