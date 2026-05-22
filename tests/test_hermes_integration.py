"""Verify that the Hermes plugin registers all expected tools and skills."""

import os

from hermes import register

EXPECTED_TOOLS = sorted(
    [
        "get_kline",
        "get_quote",
        "get_capital_flow",
        "get_news",
        "get_financials",
        "get_technical_analysis",
        "analyze_pattern",
        "get_market_indices",
        "get_sector_rankings",
        "get_stock_info",
        "get_chip_distribution",
        "get_market_stats",
        "get_fundamental_context",
        "screen_stocks",
        "run_backtest",
        "evaluate_signal",
        "resolve_stock_name",
        "check_trading_day",
        "get_trading_days",
        "calculate_ma",
        "get_volume_analysis",
        "search_stock_news",
        "search_comprehensive_intel",
        "get_social_sentiment",
        "get_trending_sentiment",
        "extract_article",
        "screen_risk",
        "detect_market_regime",
        "get_market_review",
        "run_watchlist_analysis",
        "detect_anomaly",
    ]
)

EXPECTED_SKILLS = sorted(
    [
        "bottom-volume",
        "box-oscillation",
        "bull-trend",
        "chan-theory",
        "dragon-head",
        "emotion-cycle",
        "event-driven",
        "expectation-repricing",
        "growth-quality",
        "hot-theme",
        "ma-crossover",
        "market-review",
        "one-yang-three-yin",
        "shrink-pullback",
        "stock-analysis",
        "stock-screener",
        "strategy-backtest",
        "volume-breakout",
        "wave-theory",
        "wisburg-research",
    ]
)


class MockCtx:
    def __init__(self):
        self.tools = []
        self.skills = []

    def register_tool(self, name, toolset, schema, handler):
        self.tools.append({"name": name, "toolset": toolset, "schema": schema, "handler": handler})

    def register_skill(self, name, path):
        self.skills.append({"name": name, "path": path})


def _make_ctx():
    ctx = MockCtx()
    register(ctx)
    return ctx


def test_tools_match_expected():
    ctx = _make_ctx()
    tool_names = sorted(t["name"] for t in ctx.tools)
    assert tool_names == EXPECTED_TOOLS


def test_tool_schemas_valid():
    ctx = _make_ctx()
    for tool in ctx.tools:
        assert tool["toolset"] == "stock-analysis", f"{tool['name']}: wrong toolset"
        schema = tool["schema"]
        assert "name" in schema, f"{tool['name']}: schema missing name"
        assert "description" in schema, f"{tool['name']}: schema missing description"
        assert "parameters" in schema, f"{tool['name']}: schema missing parameters"
        assert schema["parameters"]["type"] == "object", f"{tool['name']}: parameters.type != object"
        assert callable(tool["handler"]), f"{tool['name']}: handler not callable"


def test_skills_match_expected():
    ctx = _make_ctx()
    skill_names = sorted(s["name"] for s in ctx.skills)
    assert skill_names == EXPECTED_SKILLS


def test_skill_files_exist():
    ctx = _make_ctx()
    for skill in ctx.skills:
        assert os.path.isfile(skill["path"]), f"SKILL.md not found: {skill['path']}"
