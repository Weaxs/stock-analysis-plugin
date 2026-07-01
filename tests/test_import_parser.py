from tools import import_parser


class TestParse:
    def test_a_share_code(self, monkeypatch):
        # avoid live akshare call from name_resolver
        monkeypatch.setattr(import_parser, "resolve", lambda name, top=1: [])
        result = import_parser.parse("看一下 600519 的走势")
        syms = {i["symbol"] for i in result["items"]}
        assert "600519" in syms
        assert result["items"][0]["market"] == "A"

    def test_hk_code_5digit(self, monkeypatch):
        monkeypatch.setattr(import_parser, "resolve", lambda name, top=1: [])
        result = import_parser.parse("腾讯 00700.HK")
        syms = {i["symbol"] for i in result["items"]}
        assert "00700.HK" in syms

    def test_hk_code_normalized_to_5digit(self, monkeypatch):
        monkeypatch.setattr(import_parser, "resolve", lambda name, top=1: [])
        result = import_parser.parse("汇丰 0005.HK")
        syms = {i["symbol"] for i in result["items"]}
        assert "00005.HK" in syms

    def test_us_ticker(self, monkeypatch):
        monkeypatch.setattr(import_parser, "resolve", lambda name, top=1: [])
        result = import_parser.parse("Buying AAPL and TSLA today")
        syms = {i["symbol"] for i in result["items"]}
        assert "AAPL" in syms
        assert "TSLA" in syms

    def test_us_stopwords_filtered(self, monkeypatch):
        monkeypatch.setattr(import_parser, "resolve", lambda name, top=1: [])
        # THE, AND, PE should not become tickers
        result = import_parser.parse("THE AND PE ROE are metrics")
        syms = {i["symbol"] for i in result["items"]}
        assert "THE" not in syms
        assert "AND" not in syms
        assert "PE" not in syms
        assert "ROE" not in syms

    def test_hk_digits_not_double_matched_as_a_share(self, monkeypatch):
        monkeypatch.setattr(import_parser, "resolve", lambda name, top=1: [])
        result = import_parser.parse("00700.HK")
        syms = [i["symbol"] for i in result["items"]]
        # HK code should be captured; the "00700" substring should NOT create a 6-digit false positive
        assert "00700.HK" in syms

    def test_chinese_name_resolved(self, monkeypatch):
        monkeypatch.setattr(
            import_parser,
            "resolve",
            lambda name, top=1: [{"code": "600519", "name": "贵州茅台", "score": 0.95}],
        )
        result = import_parser.parse("贵州茅台")
        syms = {i["symbol"] for i in result["items"]}
        assert "600519" in syms

    def test_low_confidence_name_goes_to_unresolved(self, monkeypatch):
        # Simulate name_resolver returning a low-score fuzzy match — should reject
        monkeypatch.setattr(
            import_parser,
            "resolve",
            lambda name, top=1: [{"code": "301089", "name": "拓新药业", "score": 0.6}],
        )
        result = import_parser.parse("腾讯")
        assert "腾讯" in result["unresolved"]
        syms = [i["symbol"] for i in result["items"]]
        assert "301089" not in syms

    def test_dedupe(self, monkeypatch):
        monkeypatch.setattr(import_parser, "resolve", lambda name, top=1: [])
        result = import_parser.parse("600519 600519 AAPL AAPL")
        syms = [i["symbol"] for i in result["items"]]
        assert syms.count("600519") == 1
        assert syms.count("AAPL") == 1

    def test_noise_words_ignored(self, monkeypatch):
        # noise words like "成本" "止损" should not be sent to resolver
        seen = []
        monkeypatch.setattr(import_parser, "resolve", lambda name, top=1: seen.append(name) or [])
        import_parser.parse("成本 止损 买入")
        assert "成本" not in seen
        assert "止损" not in seen


if __name__ == "__main__":
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
