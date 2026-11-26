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


def test_us_risk_screening_deduplicates_form4a_amendments_before_veto(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test-app test@example.com")
    orig = [
        {
            "form": "4",
            "accession": "0001-26-000001",
            "transaction_date": f"2026-10-0{i}",
            "owners": [{"cik": "123", "name": "Exec A"}],
            "security": "Common Stock",
            "derivative": False,
            "transaction_code": "S",
            "shares": 1000,
            "price": 150.0,
        }
        for i in (1, 2, 3)
    ]
    amended_copies = [dict(row, form="4/A", accession="0001-26-000002") for row in orig]

    def fake_run(script, args, **kwargs):
        if script == "sec_edgar.py" and args[0] == "insider_trades":
            return {
                "since_filing_date": "2026-08-01",
                "transactions": orig + amended_copies,
                "truncated": False,
                "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
            }
        return []

    res = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=fake_run,
    )
    assert res["checks"]["insider_trades"]["sales_90d"] == 3
    assert res["veto_buy"] is not True
    insider_flags = [f for f in res["flags"] if f["category"] == "insider"]
    assert len(insider_flags) == 1 and insider_flags[0]["severity"] == "medium"

    # Even when 4/A rows revise shares/price (cannot match 1-to-1), do not trigger buy veto from amended duplicates
    revised = [dict(row, form="4/A", accession="0001-26-000003", price=151.0) for row in orig]
    res_rev = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=lambda script, args, **kw: (
            {
                "since_filing_date": "2026-08-01",
                "transactions": orig + revised,
                "truncated": False,
                "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
            }
            if script == "sec_edgar.py"
            else []
        ),
    )
    assert res_rev["veto_buy"] is not True
    assert all(f["severity"] != "high" for f in res_rev["flags"])

    # 6 original S rows where Form 4/A confirms 5 as S and reclassifies the 6th to Gift (G) -> 5 sales, no buy veto
    six_orig = [
        {
            "form": "4",
            "accession": "0001-26-000010",
            "transaction_date": f"2026-10-0{i}",
            "owners": [{"cik": "123", "name": "Exec A"}],
            "security": "Common Stock",
            "derivative": False,
            "transaction_code": "S",
            "shares": 1000,
            "price": 150.0,
        }
        for i in range(1, 7)
    ]
    amend_reclass = [dict(row, form="4/A", accession="0001-26-000011") for row in six_orig[:5]] + [
        dict(six_orig[5], form="4/A", accession="0001-26-000011", transaction_code="G", direction="other")
    ]
    res_reclass = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=lambda script, args, **kw: (
            {
                "since_filing_date": "2026-08-01",
                "transactions": six_orig + amend_reclass,
                "truncated": False,
                "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
            }
            if script == "sec_edgar.py"
            else []
        ),
    )
    assert res_reclass["checks"]["insider_trades"]["sales_90d"] == 5
    assert res_reclass["veto_buy"] is not True
    assert all(f["severity"] != "high" for f in res_reclass["flags"])

    # Successive 4/A accessions reclassifying the same 4th same-day sale to G must be idempotent (3 sales, medium flag)
    four_same_day = [
        {
            "form": "4",
            "accession": "0001-26-000020",
            "transaction_date": "2026-10-05",
            "owners": [{"cik": "123", "name": "Exec A"}],
            "security": "Common Stock",
            "derivative": False,
            "transaction_code": "S",
            "shares": shares,
            "price": 150.0,
        }
        for shares in (100, 200, 300, 400)
    ]
    amend_g_1 = dict(four_same_day[3], form="4/A", accession="0001-26-000021", transaction_code="G", direction="other")
    amend_g_2 = dict(four_same_day[3], form="4/A", accession="0001-26-000022", transaction_code="G", direction="other")
    res_idem = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=lambda script, args, **kw: (
            {
                "since_filing_date": "2026-08-01",
                "transactions": four_same_day + [amend_g_1, amend_g_2],
                "truncated": False,
                "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
            }
            if script == "sec_edgar.py"
            else []
        ),
    )
    assert res_idem["checks"]["insider_trades"]["sales_90d"] == 3
    assert any(f["category"] == "insider" and f["severity"] == "medium" for f in res_idem["flags"])

    # Unrelated same-day gift (different shares) must NOT remove a confirmed sale or suppress buy veto for 6 sales
    unrelated_gift = dict(
        six_orig[0], form="4/A", accession="0001-26-000030", transaction_code="G", direction="other", shares=250
    )
    res_gift = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=lambda script, args, **kw: (
            {
                "since_filing_date": "2026-08-01",
                "transactions": six_orig + [unrelated_gift],
                "truncated": False,
                "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
            }
            if script == "sec_edgar.py"
            else []
        ),
    )
    assert res_gift["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_gift["veto_buy"] is True

    # 2 original sales + 1 Form 4/A restating one sale at a different price -> still 2 sales, NO medium flag
    two_orig = six_orig[:2]
    restated = dict(two_orig[0], form="4/A", accession="0001-26-000040", price=152.0)
    res_restate = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=lambda script, args, **kw: (
            {
                "since_filing_date": "2026-08-01",
                "transactions": two_orig + [restated],
                "truncated": False,
                "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
            }
            if script == "sec_edgar.py"
            else []
        ),
    )
    assert res_restate["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_restate["flags"])

    def _run_us(tx_list):
        return risk_screening.screen_risk(
            "AAPL",
            "Apple",
            inputs={
                "quote": {"pe": 25, "pb": 5},
                "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
            },
            run=lambda script, args, **kw: (
                {
                    "since_filing_date": "2026-08-01",
                    "transactions": tx_list,
                    "truncated": False,
                    "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
                }
                if script == "sec_edgar.py"
                else []
            ),
        )

    # Branch A (Weaxs): 6 orig S + 1 orig Form 4 G (same shares as one S) + 4/A G adding footnote -> keep all 6 S & veto
    orig_gift = dict(
        six_orig[0],
        accession="0001-26-000050",
        transaction_code="G",
        direction="other",
        price=None,
    )
    amend_gift_fn = dict(
        orig_gift,
        form="4/A",
        accession="0001-26-000051",
        footnotes={
            "F1": "Filing amended solely to add gift recipient footnote.",
            "F99": "Unrelated filing footnote that must not affect this row.",
        },
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_orig_gift = _run_us(six_orig + [orig_gift, amend_gift_fn])
    assert res_orig_gift["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_orig_gift["checks"]["insider_trades"]["available"] is True
    assert res_orig_gift["veto_buy"] is True

    # Branch B (Weaxs): 6 orig S (6 shares) + 4/A explicitly correcting 6-share S to 7-share G -> 5 sales, no veto
    six_small = [dict(r, shares=6) for r in six_orig]
    corr_7g = dict(
        six_small[5],
        form="4/A",
        accession="0001-26-000052",
        transaction_code="G",
        direction="other",
        shares=7,
        price=None,
        footnotes={
            "F1": "Corrects previously misreported 6-share sale to a 7-share gift.",
            "F99": "Inadvertently omitted unrelated transaction on another row.",
        },
        footnote_refs={"transactionAmounts/transactionShares/value": ["F1"]},
    )
    res_corr_7g = _run_us(six_small + [corr_7g])
    assert res_corr_7g["checks"]["insider_trades"]["sales_90d"] == 5
    assert res_corr_7g["veto_buy"] is False

    # Branch C (Weaxs): 2 orig same-day S + 4/A explicitly adding omitted 3rd S -> 3 sales, medium flag
    two_same = [
        dict(six_orig[0], transaction_date="2026-10-08", shares=100, price=150.0),
        dict(six_orig[0], transaction_date="2026-10-08", shares=100, price=150.0),
    ]
    added_third = dict(
        six_orig[0],
        form="4/A",
        accession="0001-26-000053",
        transaction_date="2026-10-08",
        shares=100,
        price=150.0,
        footnotes={
            "F1": "Amends original Form 4 to report an inadvertently omitted third sale.",
            "F2": "Corrects an unrelated prior gift row.",
        },
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_added_third = _run_us(two_same + [added_third])
    assert res_added_third["checks"]["insider_trades"]["sales_90d"] == 3
    assert any(f["category"] == "insider" and f["severity"] == "medium" for f in res_added_third["flags"])

    # Branch D (CodeRabbit Major): 7 orig S incl. two same-day 100-sh ($10, $20); 4/A #1 G @ $20 + 4/A #2 G @ price=None
    # Must remove only the $20 sale (6 remaining, veto preserved), NOT also remove the $10 sale.
    seven_orig = [
        dict(six_orig[0], transaction_date="2026-10-09", shares=100, price=10.0),
        dict(six_orig[0], transaction_date="2026-10-09", shares=100, price=20.0),
    ] + [dict(six_orig[i], transaction_date=f"2026-10-1{i}") for i in range(5)]
    g_with_px = dict(seven_orig[1], form="4/A", accession="0001-26-000060", transaction_code="G", direction="other")
    g_blank_px = dict(
        seven_orig[1], form="4/A", accession="0001-26-000061", transaction_code="G", direction="other", price=None
    )
    res_blank_followup = _run_us(seven_orig + [g_with_px, g_blank_px])
    assert res_blank_followup["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_blank_followup["veto_buy"] is True

    # Same-accession blank-price gift BEFORE priced gift removes both $10 and $20 regardless of row order
    g_same_blank = dict(g_blank_px, accession="0001-26-000062")
    g_same_priced = dict(g_with_px, accession="0001-26-000062")
    assert _run_us(seven_orig + [g_same_blank, g_same_priced])["checks"]["insider_trades"]["sales_90d"] == 5
    assert _run_us(seven_orig + [g_same_priced, g_same_blank])["checks"]["insider_trades"]["sales_90d"] == 5

    # If ONLY the blank-price 4/A exists across two distinct-price candidates ($10 & $20), retain both & flag uncertainty
    res_ambig_blank = _run_us(seven_orig + [g_blank_px])
    assert res_ambig_blank["checks"]["insider_trades"]["sales_90d"] == 7
    assert res_ambig_blank["checks"]["insider_trades"]["count_uncertain"] is True

    # Branch E (CodeRabbit Minor 477): 1 orig S + two 4/A accessions each adding a different same-day S -> 3 sales, medium
    one_orig = [dict(six_orig[0], transaction_date="2026-10-15", shares=100, price=10.0)]
    add_s1 = dict(
        one_orig[0],
        form="4/A",
        accession="0001-26-000070",
        shares=200,
        price=11.0,
        footnotes={"F1": "Reports an inadvertently omitted sale."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    add_s2 = dict(
        one_orig[0],
        form="4/A",
        accession="0001-26-000071",
        shares=300,
        price=12.0,
        footnotes={"F1": "Reports an inadvertently omitted sale."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_two_adds = _run_us(one_orig + [add_s1, add_s2])
    assert res_two_adds["checks"]["insider_trades"]["sales_90d"] == 3
    assert any(f["category"] == "insider" and f["severity"] == "medium" for f in res_two_adds["flags"])

    # Branch F (CodeRabbit Minor 452): 3 orig S + 4/A restating 1st S (new price) + 4/A reclassifying 1st S to G -> 2 sales, no flag
    three_orig = six_orig[:3]
    restate_first = dict(three_orig[0], form="4/A", accession="0001-26-000080", price=155.0)
    reclass_first = dict(three_orig[0], form="4/A", accession="0001-26-000081", transaction_code="G", direction="other")
    res_restate_and_gift = _run_us(three_orig + [restate_first, reclass_first])
    assert res_restate_and_gift["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_restate_and_gift["flags"])

    # Branch G (CodeRabbit Major 590): 6 confirmed original S + explicitly added 7th S keeps veto_buy=True
    seventh_add = dict(
        six_orig[0],
        form="4/A",
        accession="0001-26-000090",
        transaction_date="2026-10-20",
        footnotes={"F1": "Reports previously omitted sale."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_seventh = _run_us(six_orig + [seventh_add])
    assert res_seventh["checks"]["insider_trades"]["sales_90d"] == 7
    assert res_seventh["veto_buy"] is True

    # Branch H (Weaxs P2 & CodeRabbit Major 594): row-order invariance — each original sale matched at most once
    orig_single = [dict(six_orig[0], transaction_date="2026-10-21", shares=100, price=150.0)]
    amend_155 = dict(orig_single[0], form="4/A", accession="0001-26-000091", price=155.0)
    amend_150 = dict(orig_single[0], form="4/A", accession="0001-26-000091", price=150.0)
    assert _run_us(orig_single + [amend_155, amend_150])["checks"]["insider_trades"]["sales_90d"] == 2
    assert _run_us(orig_single + [amend_150, amend_155])["checks"]["insider_trades"]["sales_90d"] == 2
    # With a 2nd unmatched original on the same day, unannotated amend_155 is uncertain in both row orders (no false 3rd)
    orig_pair = orig_single + [dict(six_orig[0], transaction_date="2026-10-21", shares=50, price=160.0)]
    for ordered in ([amend_155, amend_150], [amend_150, amend_155]):
        res_pair = _run_us(orig_pair + ordered)
        assert res_pair["checks"]["insider_trades"]["sales_90d"] == 2
        assert res_pair["checks"]["insider_trades"]["count_uncertain"] is True

    # Branch I (CodeRabbit Minor 608): later 4/A explicitly correcting an earlier 4/A added sale -> 3 sales, not 4
    two_other = six_orig[:2]
    add_oct22 = dict(
        six_orig[0],
        form="4/A",
        filing_date="2026-10-23",
        accession="0001-26-000092",
        transaction_date="2026-10-22",
        shares=100,
        price=10.0,
        footnotes={"F1": "Reports inadvertently omitted sale."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    corr_oct22 = dict(
        add_oct22,
        filing_date="2026-10-24",
        accession="0001-26-000093",
        price=11.0,
        footnotes={"F1": "Corrects price of previously reported sale to $11."},
        footnote_refs={"transactionAmounts/transactionPricePerShare/value": ["F1"]},
    )
    res_corr_added = _run_us(two_other + [add_oct22, corr_oct22])
    assert res_corr_added["checks"]["insider_trades"]["sales_90d"] == 3


def test_real_sec_form4_xml_boundary_with_risk_screening(monkeypatch):
    import sec_edgar

    monkeypatch.setenv("SEC_USER_AGENT", "test-app test@example.com")
    orig_xml = """<ownershipDocument>
      <issuer><issuerCik>0000320193</issuerCik></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001111111</rptOwnerCik><rptOwnerName>Exec XML</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>100</value></transactionShares>
            <transactionPricePerShare><value>150</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>50</value></transactionShares>
            <transactionPricePerShare><value>160</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>"""
    amend_xml = """<ownershipDocument>
      <issuer><issuerCik>0000320193</issuerCik></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001111111</rptOwnerCik><rptOwnerName>Exec XML</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value><footnoteId id="F1"/></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>100</value></transactionShares>
            <transactionPricePerShare><value>150</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-02</value><footnoteId id="F2"/></transactionDate>
          <transactionCoding><transactionCode>G</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>25</value></transactionShares>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
      <footnotes>
        <footnote id="F1">Reports an inadvertently omitted sale on 2026-10-01.</footnote>
        <footnote id="F2">Corrects an unrelated separate gift filing.</footnote>
      </footnotes>
      <remarks>Amendment filed to report omitted sale F1 and update F2.</remarks>
    </ownershipDocument>"""
    xml_map = {"orig.xml": orig_xml, "amend.xml": amend_xml}
    monkeypatch.setattr(sec_edgar, "sec_get", lambda url, as_json=True: xml_map[url.rsplit("/", 1)[-1]])
    orig_rows = sec_edgar.form4(
        "0000320193",
        {
            "accessionNumber": "0000320193-26-000100",
            "filingDate": "2026-10-02",
            "form": "4",
            "primaryDocument": "orig.xml",
        },
    )
    amend_rows = sec_edgar.form4(
        "0000320193",
        {
            "accessionNumber": "0000320193-26-000101",
            "filingDate": "2026-10-03",
            "form": "4/A",
            "primaryDocument": "amend.xml",
        },
    )
    assert amend_rows[0]["remarks"] == "Amendment filed to report omitted sale F1 and update F2."
    assert isinstance(amend_rows[0]["footnote_refs"], dict)
    res = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=lambda script, args, **kw: (
            {
                "since_filing_date": "2026-08-01",
                "transactions": orig_rows + amend_rows,
                "truncated": False,
                "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
            }
            if script == "sec_edgar.py"
            else []
        ),
    )
    assert res["checks"]["insider_trades"]["sales_90d"] == 3
    assert any(f["category"] == "insider" and f["severity"] == "medium" for f in res["flags"])


def test_form4_amendment_remarks_scoping_and_chronological_ledger_chain(monkeypatch):
    import sec_edgar

    monkeypatch.setenv("SEC_USER_AGENT", "test-app test@example.com")
    base_inputs = {
        "quote": {"pe": 25, "pb": 5},
        "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
    }

    def _screen(txs, *, since_filing_date="2026-08-01"):
        return risk_screening.screen_risk(
            "AAPL",
            "Apple",
            inputs=base_inputs,
            run=lambda script, args, **kw: (
                {
                    "since_filing_date": since_filing_date,
                    "transactions": txs,
                    "truncated": False,
                    "warnings": ["Includes Form 4 amendments; do not double-count original and amended transactions"],
                }
                if script == "sec_edgar.py"
                else []
            ),
        )

    orig_xml = """<ownershipDocument>
      <issuer><issuerCik>0000320193</issuerCik></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001111111</rptOwnerCik><rptOwnerName>Exec Chain</rptOwnerName></reportingOwnerId>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>100</value></transactionShares>
            <transactionPricePerShare><value>150</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>50</value></transactionShares>
            <transactionPricePerShare><value>160</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>"""

    # 1. Scoped remarks: Row 1 claims F1 ("omitted sale"), Row 2 has NO footnote and restates orig 50-sh sale,
    #    filing <remarks> says "Amending to report one omitted sale". Row 2 must NOT inherit addition intent!
    scoped_rem_xml = """<ownershipDocument>
      <issuer><issuerCik>0000320193</issuerCik></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001111111</rptOwnerCik><rptOwnerName>Exec Chain</rptOwnerName></reportingOwnerId>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value><footnoteId id="F1"/></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>100</value></transactionShares>
            <transactionPricePerShare><value>150</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>50</value></transactionShares>
            <transactionPricePerShare><value>160</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
      <footnotes>
        <footnote id="F1">Reports an inadvertently omitted sale on 2026-10-01.</footnote>
      </footnotes>
      <remarks>Amending to report one omitted sale.</remarks>
    </ownershipDocument>"""

    # 2. Mixed remarks without row footnotes: Row 0 restates 100 @ $150 -> $155, Row 1 adds distinct 200 @ $170.
    #    Must count 3 sales (NOT drop Row 1 as a correction of 50 @ $160) AND mark count_uncertain=True.
    mixed_rem_xml = """<ownershipDocument>
      <issuer><issuerCik>0000320193</issuerCik></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001111111</rptOwnerCik><rptOwnerName>Exec Chain</rptOwnerName></reportingOwnerId>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>100</value></transactionShares>
            <transactionPricePerShare><value>155</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>200</value></transactionShares>
            <transactionPricePerShare><value>170</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
      <remarks>Corrects one line and adds another omitted transaction.</remarks>
    </ownershipDocument>"""

    # 3. Chronological chain: 2 orig sales -> 4/A #1 adds omitted 101-sh sale -> 4/A #2 corrects that 101-sh sale to G.
    add_101_xml = """<ownershipDocument>
      <issuer><issuerCik>0000320193</issuerCik></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001111111</rptOwnerCik><rptOwnerName>Exec Chain</rptOwnerName></reportingOwnerId>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-03</value><footnoteId id="F1"/></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>101</value></transactionShares>
            <transactionPricePerShare><value>155</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
      <footnotes><footnote id="F1">Reports an inadvertently omitted sale.</footnote></footnotes>
    </ownershipDocument>"""

    reclass_101_xml = """<ownershipDocument>
      <issuer><issuerCik>0000320193</issuerCik></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001111111</rptOwnerCik><rptOwnerName>Exec Chain</rptOwnerName></reportingOwnerId>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-10-03</value><footnoteId id="F1"/></transactionDate>
          <transactionCoding><transactionCode>G</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>101</value></transactionShares>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
      <footnotes><footnote id="F1">Corrects previously misreported 101-share sale to a bona fide gift.</footnote></footnotes>
    </ownershipDocument>"""

    xml_map = {
        "orig.xml": orig_xml,
        "scoped.xml": scoped_rem_xml,
        "mixed.xml": mixed_rem_xml,
        "add101.xml": add_101_xml,
        "reclass101.xml": reclass_101_xml,
    }
    monkeypatch.setattr(sec_edgar, "sec_get", lambda url, as_json=True: xml_map[url.rsplit("/", 1)[-1]])

    def _parse(accn, fdate, form, doc):
        return sec_edgar.form4(
            "0000320193",
            {"accessionNumber": accn, "filingDate": fdate, "form": form, "primaryDocument": doc},
        )

    orig_rows = _parse("0000320193-26-000300", "2026-10-02", "4", "orig.xml")
    scoped_rows = _parse("0000320193-26-000301", "2026-10-03", "4/A", "scoped.xml")
    mixed_rows = _parse("0000320193-26-000302", "2026-10-03", "4/A", "mixed.xml")
    add101_rows = _parse("0000320193-26-000303", "2026-10-04", "4/A", "add101.xml")
    reclass101_rows = _parse("0000320193-26-000304", "2026-10-05", "4/A", "reclass101.xml")

    res_scoped = _screen(orig_rows + scoped_rows)
    assert res_scoped["checks"]["insider_trades"]["sales_90d"] == 3
    assert res_scoped["checks"]["insider_trades"]["count_uncertain"] is False

    res_mixed = _screen(orig_rows + mixed_rows)
    assert res_mixed["checks"]["insider_trades"]["sales_90d"] == 3
    assert res_mixed["checks"]["insider_trades"]["count_uncertain"] is True

    res_chain = _screen(orig_rows + add101_rows + reclass101_rows)
    assert res_chain["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_chain["flags"])

    # Also test share-changed explicit gift correction against the earlier 4/A added sale (101 -> 102 shares G)
    reclass_102_rows = [dict(reclass101_rows[0], shares=102)]
    res_chain_share_change = _screen(orig_rows + add101_rows + reclass_102_rows)
    assert res_chain_share_change["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_chain_share_change["flags"])

    # 4. Restore sale when a later linked 4/A explicitly corrects a previously reclassified gift back to S
    three_orig_rows = orig_rows + [dict(orig_rows[0], transaction_date="2026-10-02", shares=75, price=152.0)]
    gift_first = dict(
        three_orig_rows[0],
        form="4/A",
        accession="0000320193-26-000305",
        filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        footnotes={"F1": "Corrects previously misreported sale to a gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    restore_first = dict(
        three_orig_rows[0],
        form="4/A",
        accession="0000320193-26-000306",
        filing_date="2026-10-04",
        footnotes={"F1": "Corrects prior Form 4/A: transaction was a sale, not a gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_restore = _screen(three_orig_rows + [gift_first, restore_first])
    assert res_restore["checks"]["insider_trades"]["sales_90d"] == 3
    assert res_restore["checks"]["insider_trades"]["count_uncertain"] is False
    assert any(f["category"] == "insider" and f["severity"] == "medium" for f in res_restore["flags"])

    # Unannotated bare restatement of old S row after reclassification keeps gift state & surfaces uncertainty
    bare_restate = dict(three_orig_rows[0], form="4/A", accession="0000320193-26-000307", filing_date="2026-10-04")
    res_bare = _screen(three_orig_rows + [gift_first, bare_restate])
    assert res_bare["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_bare["checks"]["insider_trades"]["count_uncertain"] is True

    # 5. Unlinked same-day 4/A row (changed shares AND price, NO footnote/remarks) must NOT inflate count
    unlinked_amend = dict(
        orig_rows[0],
        form="4/A",
        accession="0000320193-26-000308",
        filing_date="2026-10-03",
        shares=75,
        price=155.0,
        footnotes={},
        footnote_refs={},
        remarks=None,
    )
    res_unlinked = _screen(orig_rows + [unlinked_amend])
    assert res_unlinked["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_unlinked["checks"]["insider_trades"]["count_uncertain"] is True
    assert not any(f["category"] == "insider" for f in res_unlinked["flags"])

    # 6. Plural/counted remarks ("two omitted sales") with only 1 row footnote must NOT treat all additions as claimed
    two_omit_r1 = dict(
        orig_rows[0],
        form="4/A",
        accession="0000320193-26-000309",
        filing_date="2026-10-03",
        footnotes={"F1": "Reports an omitted sale."},
        footnote_refs={"transactionDate/value": ["F1"]},
        remarks="Amending to report two omitted sales.",
    )
    two_omit_r2 = dict(
        orig_rows[1],
        form="4/A",
        accession="0000320193-26-000309",
        filing_date="2026-10-03",
        footnotes={"F1": "Reports an omitted sale."},
        footnote_refs={},
        remarks="Amending to report two omitted sales.",
    )
    # 2 rows total -> single remaining unattributed row absorbs the 2nd remarked addition -> 4 sales
    assert _screen(orig_rows + [two_omit_r1, two_omit_r2])["checks"]["insider_trades"]["sales_90d"] == 4
    # 3 rows (2 unattributed rows for 1 remaining addition) -> cannot identify which is added -> count_uncertain=True
    two_omit_r3 = dict(two_omit_r2, shares=100, price=150.0)
    res_two_omit_ambig = _screen(orig_rows + [two_omit_r1, two_omit_r2, two_omit_r3])
    assert res_two_omit_ambig["checks"]["insider_trades"]["count_uncertain"] is True

    # 7. Unrelated same-day original gift must NOT hide a linked correction of a sale to a gift
    six_sales = [dict(orig_rows[0], transaction_date=f"2026-10-0{i}") for i in range(1, 7)]
    same_day_gift = dict(
        six_sales[0], accession="0000320193-26-000310", transaction_code="G", direction="other", price=None
    )
    # 7a: priced correction matches sale ($150) rather than orig gift (None)
    corr_sale_priced = dict(
        six_sales[0],
        form="4/A",
        accession="0000320193-26-000311",
        filing_date="2026-10-08",
        transaction_code="G",
        direction="other",
        price=150.0,
    )
    res_corr_priced = _screen(six_sales + [same_day_gift, corr_sale_priced])
    assert res_corr_priced["checks"]["insider_trades"]["sales_90d"] == 5
    assert res_corr_priced["veto_buy"] is False
    # 7b: blank-price explicit correction of sale to gift
    corr_sale_text = dict(
        corr_sale_priced,
        price=None,
        footnotes={"F1": "Corrects previously misreported sale to a bona fide gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_corr_text = _screen(six_sales + [same_day_gift, corr_sale_text])
    assert res_corr_text["checks"]["insider_trades"]["sales_90d"] == 5
    assert res_corr_text["veto_buy"] is False

    # 8. Order-independent sell share-match with multiple distinct-price candidates ($10 and $20)
    s_10 = dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=10.0)
    s_20 = dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=20.0)
    a_s11 = dict(s_10, form="4/A", accession="0000320193-26-000312", filing_date="2026-10-13", price=11.0)
    a_g10 = dict(
        s_10, form="4/A", accession="0000320193-26-000313", filing_date="2026-10-14", transaction_code="G", price=10.0
    )
    a_g11 = dict(
        s_10, form="4/A", accession="0000320193-26-000314", filing_date="2026-10-15", transaction_code="G", price=11.0
    )
    res_order_a = _screen([s_10, s_20, a_s11, a_g10, a_g11])
    res_order_b = _screen([s_20, s_10, a_s11, a_g10, a_g11])
    assert res_order_a["checks"]["insider_trades"]["sales_90d"] == res_order_b["checks"]["insider_trades"]["sales_90d"]
    assert res_order_a["checks"]["insider_trades"]["count_uncertain"] is True

    # 9. Apply 90-day window to transaction_date (stale late-filed sales must not count or trigger veto)
    stale_six = [dict(orig_rows[0], transaction_date=f"2026-05-0{i}", filing_date="2026-10-20") for i in range(1, 7)]
    res_stale = _screen(stale_six, since_filing_date="2026-08-01")
    assert res_stale["checks"]["insider_trades"]["sales_90d"] == 0
    assert res_stale["veto_buy"] is False
    assert not any(f["category"] == "insider" for f in res_stale["flags"])

    # 10. dateOfOriginalSubmission binds correction to the original filing date rather than another filing's exact match
    orig_f1 = dict(orig_rows[0], accession="0000320193-26-000320", filing_date="2026-10-02", shares=100, price=10.0)
    orig_f2 = dict(orig_rows[0], accession="0000320193-26-000321", filing_date="2026-10-03", shares=100, price=20.0)
    corr_f1_to_20 = dict(
        orig_f1,
        form="4/A",
        accession="0000320193-26-000322",
        filing_date="2026-10-04",
        original_filing_date="2026-10-02",
        price=20.0,
        footnotes={"F1": "Corrects sale price to $20."},
        footnote_refs={"transactionAmounts/transactionPricePerShare/value": ["F1"]},
    )
    # Reclassifying the corrected $20 sale from filing 1 via gift on 2026-10-02 leaves filing 2 active (1 sale)
    gift_f1 = dict(
        corr_f1_to_20,
        accession="0000320193-26-000323",
        filing_date="2026-10-05",
        transaction_code="G",
        direction="other",
    )
    res_orig_date = _screen([orig_f1, orig_f2, corr_f1_to_20, gift_f1])
    assert res_orig_date["checks"]["insider_trades"]["sales_90d"] == 1
    # When two distinct original filings share the SAME filing_date, date alone cannot separate them -> uncertain
    orig_f2_same_day = dict(orig_f2, filing_date="2026-10-02")
    res_same_fdate = _screen([orig_f1, orig_f2_same_day, corr_f1_to_20])
    assert res_same_fdate["checks"]["insider_trades"]["count_uncertain"] is True


def test_hk_disclosures_resumption_notice_no_veto_and_incomplete_provider_marks_unavailable():
    base_inputs = {
        "quote": {"pe": 18, "pb": 3},
        "technical": {"signal_score": 65, "buy_signal": "BUY", "trend": {"overall": "bullish"}},
    }
    # 1. Resumption / lifting of suspension must NOT trigger high-severity buy veto even if categorized as Trading Halt
    res_resume = risk_screening.screen_risk(
        "00700.HK",
        "Tencent",
        inputs=base_inputs,
        run=lambda script, args, **kw: (
            {
                "announcements": [
                    {
                        "date": "2026-10-01",
                        "title": "LIFTING OF SUSPENSION AND RESUMPTION OF TRADING",
                        "type": "Trading Halt",
                    }
                ],
                "truncated": False,
            }
            if script == "value_data.py"
            else []
        ),
    )
    assert res_resume["veto_buy"] is False
    assert res_resume["checks"]["disclosures"]["available"] is True
    assert not any(f["category"] == "disclosure" for f in res_resume["flags"])

    # 2. HKEX "HALT IN TRADING" wording and "Trading Halt" announcement type trigger high-severity buy veto
    for ann in (
        {"date": "2026-10-01", "title": "INSIDE INFORMATION - FURTHER UPDATE AND CONTINUED HALT IN TRADING"},
        {"date": "2026-10-01", "title": "INSIDE INFORMATION - FURTHER UPDATE", "type": "Trading Halt"},
    ):
        res_halt = risk_screening.screen_risk(
            "00700.HK",
            "Tencent",
            inputs=base_inputs,
            run=lambda script, args, ann=ann, **kw: (
                {"announcements": [ann], "truncated": False} if script == "value_data.py" else []
            ),
        )
        assert res_halt["veto_buy"] is True
        assert any(f["category"] == "disclosure" and f["severity"] == "high" for f in res_halt["flags"])

    # 3. Provider-incomplete HKEX response (truncated=True with < 15 rows) marks unavailable & data_complete=False
    res_trunc = risk_screening.screen_risk(
        "00700.HK",
        "Tencent",
        inputs=base_inputs,
        run=lambda script, args, **kw: (
            {"announcements": [{"date": "2026-10-01", "title": "Profit Warning"}], "truncated": True}
            if script == "value_data.py"
            else []
        ),
    )
    assert res_trunc["checks"]["disclosures"]["available"] is False
    assert res_trunc["data_complete"] is False
    assert any(f["category"] == "disclosure" and f["severity"] == "medium" for f in res_trunc["flags"])
    assert any("incomplete/truncated" in w for w in res_trunc["warnings"])


def test_us_risk_screening_truncated_sec_marks_incomplete_and_keeps_flags(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test-app test@example.com")

    def fake_run(script, args, **kwargs):
        if script == "sec_edgar.py" and args[0] == "insider_trades":
            return {
                "transactions": [{"code": "S"}, {"code": "S"}, {"code": "S"}],
                "truncated": True,
                "warnings": ["1 filing failed"],
            }
        return []

    result = risk_screening.screen_risk(
        "AAPL",
        "Apple",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=fake_run,
    )
    assert result["data_complete"] is False
    assert result["checks"]["insider_trades"]["available"] is False
    assert result["checks"]["insider_trades"]["truncated"] is True
    assert any(f["category"] == "insider" and f["source"] == "data" for f in result["flags"])
    assert any("truncated" in w for w in result["warnings"])


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
