"""Verify that the Hermes plugin registers all expected tools and skills."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

import hermes.tools as hermes_tools
from hermes import register
from hermes.schemas import TOOL_SCHEMAS

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
        "get_sector_constituents",
        "resolve_stock_sectors",
        "get_stock_info",
        "get_chip_distribution",
        "get_market_stats",
        "get_limit_up_pool",
        "get_dragon_tiger",
        "get_hot_stocks",
        "get_margin_trading",
        "get_northbound_flow",
        "get_fundamental_context",
        "screen_stocks",
        "run_backtest",
        "evaluate_signal",
        "resolve_stock_name",
        "check_trading_day",
        "get_trading_days",
        "get_trading_phase",
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
        "diagnose_data_sources",
        "get_market_capabilities",
        "render_stock_report",
        "render_market_report",
        "build_watchlist_context",
        "analyze_position_context",
        "check_alert_rules",
        "record_signal",
        "evaluate_signals",
        "get_signal_summary",
        "get_review_history",
        "parse_stock_list",
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


def test_plugin_yaml_tools_match_schemas():
    """hermes/plugin.yaml's provides_tools is what the Hermes host advertises; it must
    list exactly the TOOL_SCHEMAS names or the manifest silently desyncs from the adapter."""
    manifest_path = Path(__file__).resolve().parent.parent / "hermes" / "plugin.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    yaml_tools = set(manifest["provides_tools"])
    schema_tools = {schema["name"] for schema in TOOL_SCHEMAS}
    missing = sorted(schema_tools - yaml_tools)
    extra = sorted(yaml_tools - schema_tools)
    assert not missing and not extra, f"plugin.yaml provides_tools out of sync: missing={missing} extra={extra}"


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


class TestRunArgv:
    """_run spawns argv lists without a shell, so tool input reaches the CLI
    verbatim and is never shell-interpreted (injection-safe on POSIX and Windows)."""

    @pytest.fixture
    def captured(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append({"cmd": cmd, "kwargs": kwargs})
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        monkeypatch.setattr(hermes_tools.subprocess, "run", fake_run)
        return calls

    def test_malicious_input_is_single_argv_element(self, captured):
        payload = "'; echo PWNED #"
        hermes_tools.get_quote({"symbol": payload})
        cmd, kwargs = captured[0]["cmd"], captured[0]["kwargs"]
        assert isinstance(cmd, list)
        assert not kwargs.get("shell", False)  # no shell (subprocess default) — nothing interprets metacharacters
        assert cmd.count(payload) == 1  # verbatim, one element — not embedded in a shell string

    def test_numeric_args_are_stringified(self, captured):
        hermes_tools.get_kline({"symbol": "600519", "count": 60})
        cmd = captured[0]["cmd"]
        assert all(isinstance(a, str) for a in cmd[2:])  # cmd[0] = python, cmd[1] = script Path
        assert cmd[-2:] == ["--count", "60"]

    def test_optional_args_appended_only_when_set(self, captured):
        hermes_tools.analyze_position_context({"symbol": "600519", "cost": 1800, "quantity": 100, "stop_loss": 1700})
        cmd = captured[0]["cmd"]
        sl = cmd.index("--stop-loss")
        assert cmd[sl + 1] == "1700"
        assert "--take-profit" not in cmd

    def test_error_contract_preserved(self, monkeypatch):
        monkeypatch.setattr(
            hermes_tools.subprocess,
            "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"),
        )
        assert json.loads(hermes_tools.get_quote({"symbol": "X"}))["error"] == "boom"

        def timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 120)

        monkeypatch.setattr(hermes_tools.subprocess, "run", timeout)
        assert "timed out" in json.loads(hermes_tools.get_quote({"symbol": "X"}))["error"]
