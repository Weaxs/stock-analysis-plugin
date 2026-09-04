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


class TestScreenerNetwork:
    """Real screener runs: full-market snapshot + sentiment gate (+ L2 enrichment).

    Assertions stay shape-level on purpose — CI runners may get partial provider
    data (rate limits), and the screener degrades gracefully by design.
    """

    def _assert_sentiment(self, out: dict):
        assert "market_sentiment" in out, f"missing market_sentiment: {list(out)}"
        sent = out["market_sentiment"]
        assert 0.8 <= sent["multiplier"] <= 1.2, f"multiplier out of range: {sent}"
        if sent["score"] is not None:
            assert 0 <= sent["score"] <= 100, f"sentiment score out of range: {sent}"

    def test_screen_a_with_sentiment(self):
        out = _run_cli("screener.py", ["screen", "--market", "A", "--top", "3"], timeout=180)
        assert isinstance(out, dict) and "candidates" in out, f"unexpected shape: {list(out)}"
        assert out["l2_enabled"] is False
        self._assert_sentiment(out)
        assert len(out["candidates"]) > 0, "expected candidates from real snapshot"
        for c in out["candidates"]:
            assert "final" in c["scores"], f"candidate missing scores.final: {c.get('symbol')}"

    def test_screen_a_l2_enrichment(self):
        out = _run_cli("screener.py", ["screen", "--market", "A", "--top", "3", "--l2"], timeout=300)
        assert isinstance(out, dict) and "candidates" in out, f"unexpected shape: {list(out)}"
        assert out["l2_enabled"] is True
        self._assert_sentiment(out)
        assert len(out["candidates"]) > 0
        for c in out["candidates"]:
            assert "enriched" in c, f"candidate missing enriched flag: {c.get('symbol')}"
            assert "final" in c["scores"]

    def test_screen_us_sentiment_path(self):
        """US market: sentiment must still resolve (index + breadth) or degrade to 1.0."""
        out = _run_cli("screener.py", ["screen", "--market", "US", "--top", "2"], timeout=180)
        assert isinstance(out, dict) and "candidates" in out, f"unexpected shape: {list(out)}"
        self._assert_sentiment(out)
