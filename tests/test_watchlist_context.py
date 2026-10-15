import pytest

from tools import watchlist_context


@pytest.mark.parametrize("workers", [0, -1, 1.5])
def test_invalid_workers_is_clean_error(workers, monkeypatch):
    monkeypatch.setattr("watchlist.gather_analysis", lambda *a, **kw: None)
    assert "error" in watchlist_context.build_context(["AAPL"], workers=workers)


def test_all_failed_analysis_is_not_a_successful_context(monkeypatch):
    monkeypatch.setattr(
        watchlist_context,
        "analyze_watchlist",
        lambda *a, **kw: {"results": {"AAPL": {"quote": None, "kline": [], "technical": {"error": "down"}}}},
    )
    monkeypatch.setattr(watchlist_context, "_run_json", lambda *a, **kw: None)
    result = watchlist_context.build_context(["AAPL"])
    assert result["summary"]["success"] == 0
    assert result["summary"]["failed"] == 1
    assert "error" in result["items"][0]


@pytest.mark.parametrize(
    "anomaly_result", [None, {"error": "Command timed out after 60s", "error_code": "timeout"}, {}]
)
def test_unavailable_anomalies_are_partial_context_with_quote_preserved(monkeypatch, anomaly_result):
    monkeypatch.setattr(
        watchlist_context,
        "analyze_watchlist",
        lambda *a, **kw: {"results": {"AAPL": {"quote": {"name": "Apple", "price": 200}}}},
    )
    monkeypatch.setattr(watchlist_context, "_run_json", lambda *a, **kw: anomaly_result)
    result = watchlist_context.build_context(["AAPL"])
    item = result["items"][0]
    assert item["price"] == 200
    assert item["name"] == "Apple"
    assert "error" not in item
    assert item["anomaly_count"] is None
    assert item["anomaly_status"] == "unavailable"
    assert item["anomaly_error"]
    assert item["warnings"]
    assert result["summary"]["success"] == 0
    assert result["summary"]["partial"] == 1
    assert result["summary"]["failed"] == 0
    assert result["meta"]["warnings"]


def test_successful_empty_anomalies_are_a_complete_context(monkeypatch):
    monkeypatch.setattr(
        watchlist_context,
        "analyze_watchlist",
        lambda *a, **kw: {"results": {"AAPL": {"quote": {"price": 200}}}},
    )
    monkeypatch.setattr(watchlist_context, "_run_json", lambda *a, **kw: {"anomalies": []})
    result = watchlist_context.build_context(["AAPL"])
    assert result["items"][0]["anomaly_status"] == "ok"
    assert result["items"][0]["anomaly_count"] == 0
    assert result["summary"]["success"] == 1
    assert result["summary"]["partial"] == 0


class TestSuggestNextTools:
    def test_a_share_bullish_adds_capital_flow(self):
        tools = watchlist_context._suggest_next_tools("A", "bullish", [], "low")
        assert "get_capital_flow" in tools

    def test_us_bullish_no_capital_flow(self):
        tools = watchlist_context._suggest_next_tools("US", "bullish", [], "low")
        assert "get_capital_flow" not in tools

    def test_high_anomaly_triggers_intel(self):
        anoms = [{"type": "macd_golden_cross", "severity": "high"}]
        tools = watchlist_context._suggest_next_tools("US", "neutral", anoms, "low")
        assert "search_comprehensive_intel" in tools

    def test_high_risk_triggers_risk_screen(self):
        tools = watchlist_context._suggest_next_tools("A", "neutral", [], "high")
        assert "screen_risk" in tools

    def test_volume_anomaly_adds_volume_tool(self):
        anoms = [{"type": "volume_spike", "severity": "medium"}]
        tools = watchlist_context._suggest_next_tools("A", "neutral", anoms, "low")
        assert "get_volume_analysis" in tools

    def test_no_duplicates(self):
        anoms = [{"type": "volume_spike", "severity": "high"}]
        tools = watchlist_context._suggest_next_tools("A", "bullish", anoms, "high")
        assert len(tools) == len(set(tools))


class TestBuildItem:
    def test_no_data_returns_error(self, monkeypatch):
        monkeypatch.setattr(watchlist_context, "_run_json", lambda *a, **kw: None)
        item = watchlist_context._build_item("600519", None)
        assert item["symbol"] == "600519"
        assert "error" in item

    def test_extracts_fields(self, monkeypatch):
        # anomaly_detect will be called via _run_json
        monkeypatch.setattr(
            watchlist_context,
            "_run_json",
            lambda script, args, **kw: {
                "anomalies": [{"type": "macd_golden_cross", "severity": "high", "direction": "bullish"}]
            },
        )
        data = {
            "quote": {"name": "贵州茅台", "price": 1500, "change_pct": 2.1},
            "technical": {"signal_score": 72, "buy_signal": "BUY", "trend": {"overall": "bullish"}},
            "risk": {"risk_level": "medium", "veto_buy": False},
        }
        item = watchlist_context._build_item("600519", data)
        assert item["score"] == 72
        assert item["trend"] == "bullish"
        assert item["buy_signal"] == "BUY"
        assert item["risk_level"] == "medium"
        assert item["market"] == "A"
        assert item["anomaly_count"] == 1
        assert "get_capital_flow" in item["next_tools"]


if __name__ == "__main__":
    import os
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"], cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(r.returncode)
