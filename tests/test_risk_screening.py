from unittest.mock import patch

from tools import risk_screening
from tools.risk_screening import _safe_float, check_news_risks, compute_risk


def test_unavailable_sources_are_not_low_risk(monkeypatch):
    monkeypatch.setattr(risk_screening, "_run_tool", lambda *a, **kw: {"error": "unavailable"})
    monkeypatch.setattr(
        risk_screening, "check_lockup", lambda *a: {"upcoming_30d": [], "flags": [], "available": False}
    )
    result = risk_screening.screen_risk("600519")
    assert result["risk_level"] == "unknown"
    assert result["risk_score"] is None
    assert result["veto_buy"] is None
    assert result["warnings"]
    assert result["data_complete"] is False


def test_death_cross_uses_technical_cross_field(monkeypatch):
    monkeypatch.setattr(
        risk_screening,
        "_run_tool",
        lambda *a, **kw: {"signal_score": 50, "macd": {"signal": "bearish", "cross": "death_cross"}},
    )
    assert any(f["description"] == "MACD死叉" for f in risk_screening.check_technical("600519")["flags"])


def test_lockups_today_are_included_and_provider_is_bounded(monkeypatch):
    import pandas as pd

    today = risk_screening.market_now("CN").strftime("%Y-%m-%d")

    def fetch(**kwargs):
        return pd.DataFrame({"解禁日期": [today]})

    monkeypatch.setattr("akshare.stock_restricted_release_queue_em", fetch)

    def run(script, args, timeout):
        assert (script, args, timeout) == ("risk_screening.py", ["lockup", "600519"], 25)
        return risk_screening._fetch_lockup(args[1])

    monkeypatch.setattr(risk_screening, "_run_tool", run)
    result = risk_screening.check_lockup("600519")
    assert result["upcoming_30d"] == [today]
    assert result["available"] is True


def test_lockup_subprocess_timeout_is_unavailable(monkeypatch):
    import subprocess

    calls = []

    def timeout(command, **kwargs):
        calls.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    assert risk_screening.check_lockup("600519")["available"] is False
    assert calls == [25]


class TestSafeFloat:
    def test_normal_float(self):
        assert _safe_float(3.14) == 3.14

    def test_string_number(self):
        assert _safe_float("42.5") == 42.5

    def test_none(self):
        assert _safe_float(None) is None

    def test_nan(self):
        assert _safe_float(float("nan")) is None

    def test_non_numeric_string(self):
        assert _safe_float("abc") is None

    def test_int(self):
        assert _safe_float(10) == 10.0

    def test_zero(self):
        assert _safe_float(0) == 0.0


class TestComputeRisk:
    def test_empty_flags(self):
        result = compute_risk([])
        assert result["risk_score"] == 0
        assert result["risk_level"] == "low"
        assert result["veto_buy"] is False

    def test_low_risk(self):
        flags = [{"severity": "low"}, {"severity": "low"}]
        result = compute_risk(flags)
        assert result["risk_score"] == 10
        assert result["risk_level"] == "low"
        assert result["veto_buy"] is False

    def test_medium_risk(self):
        flags = [{"severity": "medium"}, {"severity": "medium"}]
        result = compute_risk(flags)
        assert result["risk_score"] == 30
        assert result["risk_level"] == "medium"
        assert result["veto_buy"] is False

    def test_high_risk(self):
        flags = [{"severity": "high"}, {"severity": "high"}]
        result = compute_risk(flags)
        assert result["risk_score"] == 60
        assert result["risk_level"] == "high"
        assert result["veto_buy"] is True

    def test_cap_at_100(self):
        flags = [{"severity": "high"}] * 5
        result = compute_risk(flags)
        assert result["risk_score"] == 100
        assert result["risk_level"] == "high"

    def test_mixed_severity(self):
        flags = [
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
        ]
        result = compute_risk(flags)
        assert result["risk_score"] == 50
        assert result["risk_level"] == "medium"
        assert result["veto_buy"] is True

    def test_veto_requires_high(self):
        flags = [{"severity": "medium"}] * 4
        result = compute_risk(flags)
        assert result["risk_score"] == 60
        assert result["veto_buy"] is False


class TestCheckNewsRisksArgv:
    """Regression: search_intel.py's search subcommand only accepts --count —
    the old --max made argparse exit 2 and _run_tool silently swallowed it,
    so the news context degraded to empty forever."""

    @patch("tools.risk_screening._run_tool")
    def test_search_uses_count_param(self, mock_run):
        mock_run.return_value = []
        check_news_risks("600519", "贵州茅台")
        assert mock_run.call_count == 4
        for call in mock_run.call_args_list:
            script, argv = call[0]
            assert script == "search_intel.py"
            assert argv[0] == "search"
            assert "--count" in argv
            assert "--max" not in argv

    @patch("tools.risk_screening._run_tool")
    def test_free_text_query_stays_single_element(self, mock_run):
        mock_run.return_value = []
        check_news_risks("600519", "贵州茅台")
        for call in mock_run.call_args_list:
            _, argv = call[0]
            query = argv[1]
            assert " " in query  # e.g. "贵州茅台 减持 股东减持" — one argv element, not split
            assert query.startswith("贵州茅台 ")

    @patch("tools.risk_screening._run_tool")
    def test_categories_run_concurrently_in_stable_order(self, mock_run):
        """The four search_intel subprocesses fan out in parallel (barrier releases
        only when all four are in flight), while result keys/flags keep the declared
        category order regardless of completion order."""
        import threading

        barrier = threading.Barrier(4, timeout=10)

        def fake_run(script, args, **kw):
            barrier.wait()
            return []

        mock_run.side_effect = fake_run
        result = check_news_risks("600519", "贵州茅台")
        assert [k for k in result if k not in ("flags", "available")] == [
            "insider",
            "earnings",
            "regulatory",
            "industry",
        ]
        assert result["flags"] == []

    @patch("tools.risk_screening._run_tool")
    def test_flags_order_stable_with_hits(self, mock_run):
        # insider + regulatory hit (positions 1 and 3) — flag order follows the
        # declared category order, not arrival order
        def fake_run(script, args, **kw):
            query = args[1]
            if "减持" in query or "处罚" in query:
                return [{"title": "t"}]
            return []

        mock_run.side_effect = fake_run
        result = check_news_risks("600519", "贵州茅台")
        assert [f["category"] for f in result["flags"]] == ["insider", "regulatory"]


def test_screen_reuses_supplied_failed_inputs_and_parallelizes_other_sources():
    import threading

    barrier = threading.Barrier(5, timeout=3)
    calls = []

    def collect(script, args, **kwargs):
        calls.append((script, args))
        barrier.wait()
        if args[0] == "lockup":
            return {"upcoming_30d": [], "flags": [], "available": True}
        return []

    result = risk_screening.screen_risk("600519", inputs={"quote": None, "technical": {"error": "down"}}, run=collect)
    assert len(calls) == 5
    assert all(args[0] not in ("quote", "analyze") for _, args in calls)
    assert result["risk_level"] == "unknown"
    assert result["checks"]["valuation"]["available"] is False
    assert result["checks"]["technical"]["available"] is False
    assert result["checks"]["lockup"]["available"] is True


def test_hk_risk_screening_uses_disclosures_and_skips_lockup():
    calls = []

    def fake_run(script, args, **kwargs):
        calls.append((script, args))
        if script == "value_data.py" and args[0] == "announcements":
            return {"announcements": [{"date": "2026-10-01", "title": "Profit Warning and Trading Halt"}]}
        return []

    result = risk_screening.screen_risk(
        "00700.HK",
        "Tencent",
        inputs={
            "quote": {"pe": 18, "pb": 3},
            "technical": {"signal_score": 65, "buy_signal": "BUY", "trend": {"overall": "bullish"}},
        },
        run=fake_run,
    )
    assert result["market"] == "HK"
    assert result["data_complete"] is True
    assert result["checks"]["lockup"]["applicable"] is False
    assert result["checks"]["disclosures"]["available"] is True
    assert any(f["category"] == "disclosure" and f["severity"] == "high" for f in result["flags"])
    assert result["veto_buy"] is True
    assert not any(args[0] == "lockup" for _, args in calls)
    search_queries = [args[1] for script, args in calls if script == "search_intel.py"]
    assert any("profit warning" in q for q in search_queries)


def test_us_risk_screening_uses_sec_form4_when_configured(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test-app test@example.com")
    calls = []

    def fake_run(script, args, **kwargs):
        calls.append((script, args))
        if script == "sec_edgar.py" and args[0] == "insider_trades":
            return {"transactions": [{"code": "S"}, {"code": "S"}, {"code": "S"}]}
        return []

    result = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 28, "pb": 12},
            "technical": {"signal_score": 70, "buy_signal": "BUY", "trend": {"overall": "bullish"}},
        },
        run=fake_run,
    )
    assert result["market"] == "US"
    assert result["data_complete"] is True
    assert result["checks"]["insider_trades"]["sales_90d"] == 3
    assert any(f["category"] == "insider" and f["source"] == "data" for f in result["flags"])
    assert not any(args[0] == "lockup" for _, args in calls)


def test_us_risk_screening_without_sec_env_stays_complete(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    result = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=lambda *a, **kw: [],
    )
    assert result["data_complete"] is True
    assert result["checks"]["insider_trades"]["configured"] is False
    assert any("SEC_USER_AGENT" in w for w in result["warnings"])


def test_unsupported_market_returns_error():
    result = risk_screening.screen_risk("7203.T")
    assert "error" in result
    assert result["market"] == "JP"


def test_hk_sector_rankings_tradingview_and_etf_fallback(monkeypatch):
    import argparse

    import pandas as pd

    from tools import stock_data

    def fake_tv(region, body):
        assert region == "hongkong"
        return {
            "data": [
                {"d": ["00700", "Tencent", "Technology Services", 3.0, 4_000_000_000_000, 10_000_000_000]},
                {"d": ["09988", "Alibaba", "Technology Services", 1.0, 2_000_000_000_000, 8_000_000_000]},
                {"d": ["00005", "HSBC", "Finance", -1.0, 1_500_000_000_000, 5_000_000_000]},
            ]
        }

    monkeypatch.setattr(stock_data, "_tradingview_scan", fake_tv)
    args = argparse.Namespace(top=5, direction="both", board_type="industry", market="HK")
    res = stock_data.cmd_sector_rankings(args)
    assert res["top"][0]["name"] == "Technology Services"
    assert res["top"][0]["change_pct"] == 2.3333
    assert res["top"][0]["leading_stock"] == "Tencent"
    assert res["bottom"][0]["name"] == "Finance"
    assert res["top"][0]["market"] == "HK"

    # Concept board rejected cleanly
    bad = stock_data.cmd_sector_rankings(argparse.Namespace(top=5, direction="top", board_type="concept", market="HK"))
    assert "error" in bad

    # Fallback to HK ETF proxies when TradingView fails
    monkeypatch.setattr(stock_data, "_tradingview_scan", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    idx = pd.to_datetime(["2026-10-01", "2026-10-02"])
    closes = pd.DataFrame({"3033.HK": [4.0, 4.2], "2800.HK": [20.0, 19.8]}, index=idx)
    import yfinance as yf

    monkeypatch.setattr(yf, "download", lambda *a, **kw: {"Close": closes})
    fb = stock_data.cmd_sector_rankings(argparse.Namespace(top=5, direction="top", board_type="industry", market="HK"))
    assert fb[0]["etf"] == "3033.HK"
    assert fb[0]["proxy"] == "HK Sector ETF"
    assert fb[0]["change_pct"] == 5.0
