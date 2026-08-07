import base64
import json
import subprocess
import sys
from pathlib import Path

from tools import report_renderer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RENDERER_SCRIPT = PROJECT_ROOT / "tools" / "report_renderer.py"


class TestRender:
    def test_stock_brief(self):
        report = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "decision_type": "buy",
            "sentiment_score": 72,
            "confidence": "medium",
            "core_conclusion": {"one_sentence": "test conclusion"},
        }
        result = report_renderer.render("stock", "brief", report)
        assert "error" not in result
        assert result["format"] == "markdown"
        assert "贵州茅台" in result["content"]
        assert "600519" in result["content"]
        assert "test conclusion" in result["content"]

    def test_stock_full(self):
        report = {
            "stock_name": "Apple",
            "stock_code": "AAPL",
            "analysis_date": "2026-07-01",
            "decision_type": "hold",
            "sentiment_score": 55,
            "confidence": "low",
            "data_perspective": {
                "trend_status": {"ma_alignment": "neutral", "is_bullish": None},
                "price_position": {
                    "current_price": 200,
                    "bias_ma5": 1.2,
                    "support_level": 190,
                    "resistance_level": 210,
                },
                "volume_analysis": {"volume_ratio": 1.1, "volume_status": "normal", "turnover_rate": 2.0},
                "chip_structure": {},
            },
            "intelligence": {},
            "battle_plan": {},
            "risk_screening": {"risk_level": "low", "risk_score": 10, "flags": []},
        }
        result = report_renderer.render("stock", "full", report)
        assert "error" not in result
        assert "AAPL" in result["content"]

    def test_unknown_kind(self):
        result = report_renderer.render("bogus", "full", {})
        assert "error" in result

    def test_unknown_template(self):
        result = report_renderer.render("stock", "bogus", {})
        assert "error" in result

    def test_meta_present(self):
        result = report_renderer.render(
            "stock",
            "brief",
            {"stock_name": "x", "stock_code": "y", "decision_type": "hold", "sentiment_score": 50, "confidence": "low"},
        )
        assert result["meta"]["provider"] == "renderer"
        assert result["meta"]["template"].endswith(".j2")


class TestCLI:
    """End-to-end: run the CLI the way pi/hermes/openclaw do (subprocess + --input-b64)."""

    def test_stock_brief_via_subprocess(self):
        report = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "decision_type": "buy",
            "sentiment_score": 72,
            "confidence": "medium",
            "core_conclusion": {"one_sentence": "cli test"},
        }
        payload = base64.b64encode(json.dumps(report, ensure_ascii=False).encode("utf-8")).decode("ascii")
        r = subprocess.run(
            [sys.executable, str(RENDERER_SCRIPT), "stock", "--template", "brief", "--input-b64", payload],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        out = json.loads(r.stdout)
        assert "error" not in out, f"renderer error (is jinja2 installed?): {out.get('error')}"
        assert "贵州茅台" in out["content"]
        assert "cli test" in out["content"]


if __name__ == "__main__":
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
