import os

from tools import diagnostics


class TestCheckProvider:
    def test_efinance_available(self, monkeypatch):
        # efinance has no env requirement and should be installed in the test env
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        result = diagnostics._check_provider("efinance")
        assert result["name"] == "efinance"
        assert isinstance(result["available"], bool)
        assert result["markets"] == ["A"]

    def test_missing_env_marks_unavailable(self, monkeypatch):
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        result = diagnostics._check_provider("tushare")
        # regardless of package presence, missing env → unavailable
        assert result["available"] is False
        assert "TUSHARE_TOKEN" in (result["reason"] or "")

    def test_env_present_still_needs_package(self, monkeypatch):
        monkeypatch.setenv("FINNHUB_API_KEY", "fake")
        result = diagnostics._check_provider("finnhub")
        # finnhub is now checked against the `finnhub` python package (not `requests`).
        # If the package is installed AND env is set → available; otherwise reason names the gap.
        if result["available"]:
            assert result["reason"] is None
        else:
            assert "finnhub" in (result["reason"] or "")

    def test_missing_finnhub_package_reported(self, monkeypatch):
        # Simulate finnhub package missing even though env is set
        monkeypatch.setenv("FINNHUB_API_KEY", "fake")
        import importlib

        real_import = importlib.import_module

        def fake_import(name, *a, **kw):
            if name == "finnhub":
                raise ImportError("No module named 'finnhub'")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(importlib, "import_module", fake_import)
        result = diagnostics._check_provider("finnhub")
        assert result["available"] is False
        assert "finnhub" in (result["reason"] or "")

    def test_longbridge_checks_longport_package(self, monkeypatch):
        # Env vars set but simulate longport.openapi missing
        for v in ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN"):
            monkeypatch.setenv(v, "x")
        import importlib

        real_import = importlib.import_module

        def fake_import(name, *a, **kw):
            if name == "longport.openapi":
                raise ImportError("No module named 'longport'")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(importlib, "import_module", fake_import)
        result = diagnostics._check_provider("longbridge")
        assert result["available"] is False
        assert "longport" in (result["reason"] or "")


class TestDiagnose:
    def test_all_markets(self, monkeypatch):
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        result = diagnostics.diagnose("all")
        markets = {m["market"] for m in result["markets"]}
        assert markets == {"A", "HK", "US"}
        assert "meta" in result
        assert result["meta"]["provider"] == "diagnostics"

    def test_single_market(self):
        result = diagnostics.diagnose("A")
        assert len(result["markets"]) == 1
        assert result["markets"][0]["market"] == "A"

    def test_market_available_flag(self, monkeypatch):
        # If NO provider works for a market, available=False
        # Force by mocking _check_provider to always fail
        monkeypatch.setattr(
            diagnostics,
            "_check_provider",
            lambda name: {
                "name": name,
                "available": False,
                "markets": diagnostics.PROVIDERS[name][2],
                "reason": "forced",
            },
        )
        result = diagnostics.diagnose("A")
        assert result["markets"][0]["available"] is False
        assert any("no working" in w for w in result["markets"][0]["warnings"])

    def test_warns_when_no_tushare(self, monkeypatch):
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        result = diagnostics.diagnose("A")
        warnings = result["markets"][0]["warnings"]
        assert any("TUSHARE_TOKEN" in w for w in warnings)


if __name__ == "__main__":
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
