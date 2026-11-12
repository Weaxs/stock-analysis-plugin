from tools import capabilities


class TestGetCapabilities:
    def test_a_share_supports_chip_distribution(self):
        result = capabilities.get_capabilities("A")
        assert "get_chip_distribution" in result["supported"]
        assert "get_capital_flow" in result["supported"]
        assert "get_social_sentiment" in result["supported"]

    def test_hk_rejects_a_share_only_tools(self):
        result = capabilities.get_capabilities("HK")
        unsupported_tools = {u["tool"] for u in result["unsupported"]}
        assert "get_chip_distribution" in unsupported_tools
        assert "get_capital_flow" in unsupported_tools
        assert "screen_risk" in unsupported_tools
        # kline is universal
        assert "get_kline" in result["supported"]
        assert "get_social_sentiment" in result["supported"]

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


class TestNewMarkets:
    def test_jp_supports_generic_tools(self):
        result = capabilities.get_capabilities("JP")
        for tool in ("get_kline", "get_quote", "get_news", "get_financials", "get_market_indices"):
            assert tool in result["supported"]

    def test_jp_rejects_a_share_only_tools_but_supports_regime(self):
        result = capabilities.get_capabilities("JP")
        unsupported = {u["tool"] for u in result["unsupported"]}
        assert "get_chip_distribution" in unsupported
        assert "get_capital_flow" in unsupported
        assert "get_market_review" in unsupported
        assert "screen_stocks" in unsupported
        for m in ("JP", "KR", "TW"):
            assert "detect_market_regime" in capabilities.get_capabilities(m)["supported"]

    def test_yfinance_markets_extend_dividends_earnings_and_sec_filings(self):
        for m in ("US", "JP", "KR", "TW"):
            assert "get_dividends" in capabilities.get_capabilities(m)["supported"]
        for m in ("US", "JP"):
            assert "get_earnings_calendar" in capabilities.get_capabilities(m)["supported"]
        assert "get_earnings_calendar" not in capabilities.get_capabilities("KR")["supported"]
        assert "get_announcements" in capabilities.get_capabilities("US")["supported"]
        assert "get_announcements" not in capabilities.get_capabilities("HK")["supported"]


def test_name_resolution_capability_matches_hk_us_support():
    for market in ("A", "HK", "US"):
        assert "resolve_stock_name" in capabilities.get_capabilities(market)["supported"]
    for market in ("JP", "KR", "TW"):
        assert "resolve_stock_name" in {row["tool"] for row in capabilities.get_capabilities(market)["unsupported"]}


def test_matrix_covers_every_registered_tool_exactly():

    from hermes.schemas import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_watchlist" in names
    assert set(capabilities.TOOL_MATRIX) == names
    for market in capabilities.ALL_MARKETS:
        result = capabilities.get_capabilities(market)
        assert len(result["supported"]) + len(result["unsupported"]) == len(names)
        assert "get_watchlist" in result["supported"]


def test_kr_tw_supported():
    for m in ("KR", "TW"):
        result = capabilities.get_capabilities(m)
        assert "get_kline" in result["supported"]
        assert "get_quote" in result["supported"]


class TestShortTermAndSignalTools:
    A_ONLY = ("get_limit_up_pool", "get_dragon_tiger", "get_hot_stocks", "get_margin_trading", "get_northbound_flow")
    SIGNAL = ("record_signal", "evaluate_signals", "get_signal_summary")

    def test_a_share_supports_new_tools(self):
        result = capabilities.get_capabilities("A")
        for tool in self.A_ONLY + self.SIGNAL + ("get_trading_phase",):
            assert tool in result["supported"]

    def test_non_a_markets_reject_a_share_only(self):
        for m in ("HK", "US", "JP", "KR", "TW"):
            unsupported = {u["tool"] for u in capabilities.get_capabilities(m)["unsupported"]}
            for tool in self.A_ONLY:
                assert tool in unsupported, f"{tool} should be unsupported in {m}"

    def test_phase_and_signal_tools_all_markets(self):
        for m in capabilities.ALL_MARKETS:
            supported = capabilities.get_capabilities(m)["supported"]
            for tool in self.SIGNAL + ("get_trading_phase",):
                assert tool in supported, f"{tool} should be supported in {m}"


def test_monthly_trend_declares_only_tested_market_scope():
    for market in ("A", "HK", "US"):
        assert "analyze_trend_strategy" in capabilities.get_capabilities(market)["supported"]
    for market in ("JP", "KR", "TW"):
        assert "analyze_trend_strategy" in {row["tool"] for row in capabilities.get_capabilities(market)["unsupported"]}
