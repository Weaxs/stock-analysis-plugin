import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from tools import report_renderer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RENDERER_SCRIPT = PROJECT_ROOT / "tools" / "report_renderer.py"
MARKET_SCHEMA = PROJECT_ROOT / "schemas" / "market_review_schema.json"


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

    def test_market_full_accepts_documented_minimum(self):
        schema = json.loads(MARKET_SCHEMA.read_text(encoding="utf-8"))
        report = {
            "market": "A",
            "review_date": "2026-09-16",
            "temperature": {"score": 50, "signal": "yellow"},
            "strategy_stance": "balanced",
        }
        assert set(schema["required"]) <= report.keys()
        result = report_renderer.render("market", "full", report)
        assert "error" not in result
        assert "A 股大盘复盘" in result["content"]

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


class TestMarketSave:
    """--save archives the report object to the review JSONL store (side effect)."""

    MINIMAL_REPORT = {
        "market": "A",
        "review_date": "2026-09-22",
        "temperature": {"score": 50, "signal": "yellow"},
        "strategy_stance": "balanced",
    }

    def _run_main(self, monkeypatch, capsys, store, extra_args):
        monkeypatch.setenv("STOCK_REVIEW_STORE", str(store))
        payload = base64.b64encode(json.dumps(self.MINIMAL_REPORT, ensure_ascii=False).encode("utf-8")).decode("ascii")
        monkeypatch.setattr(sys, "argv", ["report_renderer.py", "market", "--input-b64", payload, *extra_args])
        report_renderer.main()
        return json.loads(capsys.readouterr().out)

    def test_save_archives_report(self, tmp_path, monkeypatch, capsys):
        store = tmp_path / "reviews.jsonl"
        out = self._run_main(monkeypatch, capsys, store, ["--save"])
        assert "error" not in out
        assert out["archived"]["saved"] is True
        assert out["archived"]["date"] == "2026-09-22"
        # the archived block never echoes the absolute store path to the host
        assert "store" not in out["archived"]
        lines = store.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == self.MINIMAL_REPORT

    def test_no_save_writes_nothing(self, tmp_path, monkeypatch, capsys):
        store = tmp_path / "reviews.jsonl"
        out = self._run_main(monkeypatch, capsys, store, [])
        assert "error" not in out
        assert "archived" not in out
        assert not store.exists()

    def test_save_via_subprocess(self, tmp_path):
        """End-to-end the way hosts call it: env override + argv, JSON on stdout."""
        store = tmp_path / "reviews.jsonl"
        payload = base64.b64encode(json.dumps(self.MINIMAL_REPORT, ensure_ascii=False).encode("utf-8")).decode("ascii")
        r = subprocess.run(
            [sys.executable, str(RENDERER_SCRIPT), "market", "--input-b64", payload, "--save"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            env={**os.environ, "STOCK_REVIEW_STORE": str(store)},
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        out = json.loads(r.stdout)
        assert "error" not in out, f"renderer error (is jinja2 installed?): {out.get('error')}"
        assert out["archived"]["saved"] is True
        assert len(store.read_text(encoding="utf-8").splitlines()) == 1

    def test_save_failure_yields_archived_error_block(self, tmp_path, monkeypatch, capsys):
        # STOCK_REVIEW_STORE points at an existing directory → save_review's open()
        # raises IsADirectoryError (OSError) → archived carries a clean error block;
        # the render itself still succeeded, and no traceback leaks.
        out = self._run_main(monkeypatch, capsys, tmp_path, ["--save"])
        assert "error" not in out
        assert "saved" not in out["archived"]
        assert "error" in out["archived"]
        assert "Traceback" not in out["archived"]["error"]


if __name__ == "__main__":
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
