"""Layer 1 e2e: host handler → subprocess → real Python tool → JSON round-trip.

Verifies that pi/hermes/openclaw all wire the SAME 4 non-network tools correctly.
No akshare/yfinance/LLM calls — pure host↔python plumbing.

Covers 4 tools chosen because they don't need external data:
- get_market_capabilities (static map)
- parse_stock_list (regex + resolver monkeypatch)
- render_stock_report (jinja only)
- diagnose_data_sources (pkg/env probe)
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"


def _run_cli(script: str, args: list[str], timeout: int = 30) -> dict:
    """Run tools/<script> with args, return parsed JSON stdout."""
    cmd = [sys.executable, str(TOOLS_DIR / script)] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, f"exit {r.returncode}\nstderr: {r.stderr}"
    return json.loads(r.stdout)


class TestCapabilitiesRoundTrip:
    def test_hk_via_cli(self):
        out = _run_cli("capabilities.py", ["get", "--market", "HK"])
        assert out["market"] == "HK"
        assert "get_kline" in out["supported"]
        assert out["meta"]["provider"] == "capabilities"
        # HK must reject A-share-only tool
        unsupported = {u["tool"] for u in out["unsupported"]}
        assert "get_chip_distribution" in unsupported


class TestParseStockListRoundTrip:
    def test_mixed_symbols_via_cli(self):
        import base64

        text = "600519 00700.HK AAPL"
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        out = _run_cli("import_parser.py", ["parse", "--text-b64", payload])
        symbols = {i["symbol"] for i in out["items"]}
        assert "600519" in symbols
        assert "00700.HK" in symbols
        assert "AAPL" in symbols
        assert out["meta"]["provider"] == "import_parser"


class TestRenderStockReportRoundTrip:
    def test_brief_via_cli(self):
        import base64

        report = {
            "stock_name": "e2e-test",
            "stock_code": "600519",
            "decision_type": "buy",
            "sentiment_score": 60,
            "confidence": "medium",
        }
        payload = base64.b64encode(json.dumps(report, ensure_ascii=False).encode("utf-8")).decode("ascii")
        out = _run_cli(
            "report_renderer.py",
            ["stock", "--template", "brief", "--input-b64", payload],
        )
        assert out["format"] == "markdown"
        assert "e2e-test" in out["content"]
        assert "600519" in out["content"]


class TestDiagnoseDataSourcesRoundTrip:
    def test_all_markets_via_cli(self):
        out = _run_cli("diagnostics.py", ["check", "--market", "all"])
        assert out["meta"]["provider"] == "diagnostics"
        markets = {m["market"] for m in out["markets"]}
        assert markets == {"A", "HK", "US"}


# ---------------------------------------------------------------------------
# Hermes host: register() → handler() → subprocess → parse
# ---------------------------------------------------------------------------


class TestHermesE2E:
    """Hermes hosts Python natively via subprocess. Verify each handler round-trips."""

    @pytest.fixture(scope="class")
    def hermes_ctx(self):
        from hermes import register

        class MockCtx:
            def __init__(self):
                self.handlers = {}

            def register_tool(self, name, toolset, schema, handler):
                self.handlers[name] = handler

            def register_skill(self, name, path):
                pass

        ctx = MockCtx()
        register(ctx)
        return ctx

    def test_get_market_capabilities(self, hermes_ctx):
        result_raw = hermes_ctx.handlers["get_market_capabilities"]({"market": "A"})
        result = json.loads(result_raw)
        assert "error" not in result
        assert result["market"] == "A"
        assert result["meta"]["provider"] == "capabilities"

    def test_parse_stock_list(self, hermes_ctx):
        result_raw = hermes_ctx.handlers["parse_stock_list"]({"text": "AAPL 600519"})
        result = json.loads(result_raw)
        symbols = {i["symbol"] for i in result["items"]}
        assert "AAPL" in symbols
        assert "600519" in symbols

    def test_render_stock_report(self, hermes_ctx):
        report = {
            "stock_name": "hermes-e2e",
            "stock_code": "AAPL",
            "decision_type": "hold",
            "sentiment_score": 50,
            "confidence": "low",
        }
        result_raw = hermes_ctx.handlers["render_stock_report"]({"report": report, "template": "brief"})
        result = json.loads(result_raw)
        assert "error" not in result
        assert "hermes-e2e" in result["content"]

    def test_diagnose_data_sources(self, hermes_ctx):
        result_raw = hermes_ctx.handlers["diagnose_data_sources"]({"market": "A"})
        result = json.loads(result_raw)
        assert "meta" in result
        assert result["meta"]["provider"] == "diagnostics"
