"""Verify that the Hermes plugin registers all expected tools and skills."""

import ast
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import hermes.tools as hermes_tools
from hermes import register
from hermes.schemas import TOOL_SCHEMAS


def test_smoke_hook_records_only_tool_name(tmp_path, monkeypatch):
    from tests.e2e.hermes_tool_spy import register as register_spy

    hooks = {}
    log = tmp_path / "tools.jsonl"
    monkeypatch.setenv("HERMES_SMOKE_TOOL_LOG", str(log))
    register_spy(SimpleNamespace(register_hook=lambda name, callback: hooks.update({name: callback})))
    hooks["post_tool_call"](tool_name="get_quote", args={"secret": "private"}, result='{"price":100.25}', status="ok")
    assert log.read_text() == '"get_quote"\n'


@pytest.mark.parametrize(
    ("status", "result"),
    [
        ("blocked", '{"price":100}'),
        ("error", '{"price":100}'),
        ("cancelled", '{"price":100}'),
        ("ok", '{"error":"provider unavailable"}'),
        ("ok", "invalid JSON"),
        ("ok", '{"price":null}'),
        ("ok", '{"price":true}'),
        ("ok", '{"price":0}'),
        ("ok", '{"price":NaN}'),
    ],
)
def test_smoke_hook_rejects_failed_or_unusable_quotes(tmp_path, monkeypatch, status, result):
    from tests.e2e.hermes_tool_spy import register as register_spy

    hooks = {}
    log = tmp_path / "tools.jsonl"
    monkeypatch.setenv("HERMES_SMOKE_TOOL_LOG", str(log))
    register_spy(SimpleNamespace(register_hook=lambda name, callback: hooks.update({name: callback})))
    hooks["post_tool_call"](tool_name="get_quote", status=status, result=result)
    assert not log.exists()


@pytest.mark.parametrize("name", ["render_stock_report", "render_market_report"])
@pytest.mark.parametrize("final", [False, True])
def test_report_final_flag_reaches_shared_cli(name, final, monkeypatch):
    calls = []
    payload = '{"format":"markdown","content":"# report","final":true}'
    monkeypatch.setattr(hermes_tools, "_run", lambda script, argv: calls.append((script, argv)) or payload)
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == name)
    assert schema["parameters"]["properties"]["final"]["type"] == "boolean"
    assert getattr(hermes_tools, name)({"report": {}, "final": final}) == payload
    assert calls[0][0] == "report_renderer.py"
    assert ("--final" in calls[0][1]) is final


def test_technical_analysis_preserves_independent_temperature(monkeypatch):
    payload = (Path(__file__).parent / "fixtures/trend_temperature.json").read_text(encoding="utf-8")
    monkeypatch.setattr(
        hermes_tools.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload)
    )
    assert hermes_tools.get_technical_analysis({"symbol": "TEST"}) == payload
    description = next(schema["description"] for schema in TOOL_SCHEMAS if schema["name"] == "get_technical_analysis")
    assert "实验性" in description and "非星球指标" in description
    assert "validation_status=not_validated" in description and "direction" in description
    assert "trend_horizons" in description


def test_wheel_declares_all_shared_runtime_dependencies():
    root = Path(__file__).resolve().parents[1]
    from packaging.requirements import Requirement

    manifest = (root / "pyproject.toml").read_text(encoding="utf-8")
    wheel_deps = ast.literal_eval(manifest.split("dependencies = ", 1)[1].split("[project.urls]", 1)[0].strip())
    cli_deps = (root / "tools" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert {str(Requirement(d)) for d in wheel_deps} == {str(Requirement(d)) for d in cli_deps if d.strip()}


EXPECTED_TOOLS = sorted(
    [
        "compare_stocks",
        "analyze_trend_strategy",
        "calculate_rotation_metrics",
        "backtest_rotation_allocation",
        "analyze_disclosure_changes",
        "analyze_portfolio_risk",
        "calculate_position_size",
        "analyze_event_study",
        "validate_backtest",
        "compare_analysis_snapshots",
        "get_announcements",
        "get_us_holdings",
        "get_insider_trades",
        "get_valuation_history",
        "get_index_valuation",
        "get_earnings_calendar",
        "get_shareholder_structure",
        "get_etf_details",
        "get_dividends",
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
        "sector-rotation",
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


def test_stateless_inputs_reach_cli_and_transport_is_removed(monkeypatch):
    fixtures = json.loads((Path(__file__).parent / "fixtures/stateless_analysis.json").read_text(encoding="utf-8"))
    captured = []

    def execute(script, argv):
        path = Path(argv[2])
        captured.append((script, argv[0], path, json.loads(path.read_text(encoding="utf-8"))))
        return '{"ok":true}'

    monkeypatch.setattr(hermes_tools, "_run", execute)
    for name, fixture in fixtures.items():
        result = getattr(hermes_tools, name)(fixture["args"])
        script, command, path, payload = captured[-1]
        assert script == "stateless_analysis.py"
        assert command == fixture["command"]
        assert payload == fixture["args"]
        assert not path.exists()
        assert json.loads(result) == {"ok": True}


def test_stateless_transport_removed_after_failure(monkeypatch):
    paths = []

    def execute(script, argv):
        paths.append(Path(argv[2]))
        raise RuntimeError("private input must not be echoed")

    monkeypatch.setattr(hermes_tools, "_run", execute)
    result = hermes_tools.compare_analysis_snapshots({"previous": {}, "current": {}})
    assert "error" in json.loads(result)
    assert "private input" not in result
    assert not paths[0].exists()


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
        assert "Python command failed" in json.loads(hermes_tools.get_quote({"symbol": "X"}))["error"]
        assert "boom" not in hermes_tools.get_quote({"symbol": "X"})

        def timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 120)

        monkeypatch.setattr(hermes_tools.subprocess, "run", timeout)
        assert "timed out" in json.loads(hermes_tools.get_quote({"symbol": "X"}))["error"]


@pytest.mark.parametrize(
    "name,args,command",
    [
        ("get_valuation_history", {"symbol": "600519"}, "valuation_history"),
        ("get_index_valuation", {"symbol": "000300"}, "index_valuation"),
        ("get_earnings_calendar", {"report_date": "2026-09-30"}, "earnings_calendar"),
        ("get_shareholder_structure", {"symbol": "0700.HK"}, "shareholder_structure"),
        ("get_etf_details", {"symbol": "510300", "benchmark": "sh000300"}, "etf_details"),
        ("get_dividends", {"mode": "ranking"}, "dividends"),
    ],
)
def test_value_cli_dispatch(monkeypatch, name, args, command):
    calls = []
    monkeypatch.setattr(hermes_tools, "_run", lambda script, argv: calls.append((script, argv)) or "{}")
    getattr(hermes_tools, name)(args)
    assert calls[0][0] == "value_data.py"
    assert calls[0][1][0] == command


def test_financial_and_margin_flags(monkeypatch):
    calls = []
    monkeypatch.setattr(hermes_tools, "_run", lambda script, argv: calls.append(argv) or "{}")
    hermes_tools.get_financials({"symbol": "600519", "periods": 10, "annual": True})
    assert "--annual" in calls[-1]
    hermes_tools.get_margin_trading({"symbol": "600519", "include_ratio": True})
    assert "--include-ratio" in calls[-1]


def test_invalid_financial_periods_are_forwarded_for_core_validation(monkeypatch):
    calls = []
    monkeypatch.setattr(hermes_tools, "_run", lambda script, argv: calls.append(argv) or "{}")
    hermes_tools.get_financials({"symbol": "600519", "periods": 0})
    assert calls[-1] == ["financials", "600519", "--periods", 0]


def test_hk_calendar_and_dividend_market_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(hermes_tools, "_run", lambda script, argv: calls.append(argv) or "{}")
    hermes_tools.get_dividends({"mode": "ranking", "market": "HK"})
    assert calls[-1][calls[-1].index("--market") + 1] == "HK"
    hermes_tools.get_earnings_calendar({"symbol": "0700.HK"})
    assert calls[-1][calls[-1].index("--report-date") + 1] == ""
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "get_earnings_calendar")
    assert "report_date" not in schema["parameters"].get("required", [])


def test_full_disclosed_equities_flag_is_opt_in(monkeypatch):
    calls = []
    monkeypatch.setattr(hermes_tools, "_run", lambda script, argv: calls.append(argv) or "{}")
    hermes_tools.get_etf_details({"symbol": "510300", "include_full_holdings": True})
    assert "--include-full-holdings" in calls[-1]
    hermes_tools.get_etf_details({"symbol": "510300"})
    assert "--include-full-holdings" not in calls[-1]


@pytest.mark.parametrize(
    "name,args,script,argv",
    [
        (
            "get_announcements",
            {
                "symbol": "600519",
                "category": "inquiry",
                "start_date": "2026-01-01",
                "end_date": "2026-10-01",
                "top": 80,
            },
            "value_data.py",
            [
                "announcements",
                "600519",
                "--category",
                "inquiry",
                "--start-date",
                "2026-01-01",
                "--end-date",
                "2026-10-01",
                "--top",
                80,
            ],
        ),
        ("get_us_holdings", {"cik": "0001067983"}, "sec_edgar.py", ["holdings", "0001067983"]),
        (
            "get_insider_trades",
            {"symbol": "AAPL", "days": 30, "top": 2},
            "sec_edgar.py",
            ["insider_trades", "AAPL", "--days", 30, "--top", 2],
        ),
    ],
)
def test_disclosure_cli_dispatch(monkeypatch, name, args, script, argv):
    calls = []
    monkeypatch.setattr(hermes_tools, "_run", lambda script, argv: calls.append((script, argv)) or "{}")
    getattr(hermes_tools, name)(args)
    assert calls == [(script, argv)]


def test_disclosure_error_json_reaches_hermes(monkeypatch):
    payload = '{"error": "Set SEC_USER_AGENT to your application name and real contact email"}'
    monkeypatch.setattr(
        hermes_tools.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, stdout=payload, stderr=""),
    )
    assert hermes_tools.get_us_holdings({"cik": "1067983"}) == payload


def test_issuer_holdings_mode_is_forwarded(monkeypatch):
    calls = []
    monkeypatch.setattr(hermes_tools, "_run", lambda script, argv: calls.append((script, argv)) or "{}")
    hermes_tools.get_us_holdings({"cik": "0000320193", "mode": "issuer"})
    assert calls == [("sec_edgar.py", ["holdings", "0000320193", "--mode", "issuer"])]


def test_cold_issuer_lookup_has_time_for_valid_200_second_download(monkeypatch):
    def run(cmd, **kwargs):
        if kwargs["timeout"] < 200:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, 0, stdout='{"query_type":"issuer","institutions":[]}', stderr="")

    monkeypatch.setattr(hermes_tools.subprocess, "run", run)
    result = json.loads(hermes_tools.get_us_holdings({"cik": "320193", "mode": "issuer"}))
    assert result["query_type"] == "issuer"


@pytest.mark.parametrize(
    "script,argv,expected",
    [
        ("stock_data.py", ["quote", "600519"], 150),
        ("screener.py", ["screen"], 1860),
        ("sec_edgar.py", ["holdings", "320193"], 330),
    ],
)
def test_host_budgets_and_evidence_relay(monkeypatch, script, argv, expected):
    payload = '{"source":"fixture","price":100,"fetched_at":"2026-10-14T00:00:00+00:00","data_as_of":null}'

    def run(command, **kwargs):
        assert kwargs["timeout"] == expected
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")

    monkeypatch.setattr(hermes_tools.subprocess, "run", run)
    assert hermes_tools._run(script, argv) == payload


def test_monthly_trend_native_request_real_cli_roundtrip():
    result = json.loads(hermes_tools.analyze_trend_strategy({"symbols": ["600036", "AAPL"]}))
    assert result["error_code"] == "invalid_request"
    schema = next(row for row in TOOL_SCHEMAS if row["name"] == "analyze_trend_strategy")
    assert schema["parameters"]["required"] == ["symbols"]
    assert "input_file" not in schema["parameters"]["properties"]
    assert "not_validated" in schema["description"]


def test_monthly_trend_native_request_transport_is_removed(monkeypatch):
    seen = []
    payload = {"symbols": ["AAPL", "MSFT"], "held_symbols": ["AAPL"]}

    def run(script, argv):
        path = Path(argv[-1])
        seen.append(path)
        assert script == "trend_strategy.py" and argv[:2] == ["fetch", "--input"]
        assert json.loads(path.read_text()) == payload
        return '{"transport":"ok"}'

    monkeypatch.setattr(hermes_tools, "_run", run)
    assert json.loads(hermes_tools.analyze_trend_strategy(payload)) == {"transport": "ok"}
    assert not seen[0].exists()
