from tools import capabilities


class TestGetCapabilities:
    def test_a_share_supports_chip_distribution(self):
        result = capabilities.get_capabilities("A")
        assert "get_chip_distribution" in result["supported"]
        assert "get_capital_flow" in result["supported"]

    def test_hk_rejects_a_share_only_tools(self):
        result = capabilities.get_capabilities("HK")
        unsupported_tools = {u["tool"] for u in result["unsupported"]}
        assert "get_chip_distribution" in unsupported_tools
        assert "get_capital_flow" in unsupported_tools
        # kline is universal
        assert "get_kline" in result["supported"]

    def test_us_rejects_a_share_only_tools(self):
        result = capabilities.get_capabilities("US")
        unsupported_tools = {u["tool"] for u in result["unsupported"]}
        assert "get_capital_flow" in unsupported_tools

    def test_unsupported_has_reason(self):
        result = capabilities.get_capabilities("HK")
        for u in result["unsupported"]:
            assert u["reason"]

    def test_case_insensitive_market(self):
        r1 = capabilities.get_capabilities("a")
        r2 = capabilities.get_capabilities("A")
        assert r1["supported"] == r2["supported"]

    def test_meta_present(self):
        result = capabilities.get_capabilities("A")
        assert result["meta"]["provider"] == "capabilities"
        assert "fetched_at" in result["meta"]


if __name__ == "__main__":
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
