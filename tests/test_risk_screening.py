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
            return {
                "since_filing_date": "2026-08-01",
                "transactions": [{"code": "S", "transaction_date": f"2026-10-0{i}"} for i in (1, 2, 3)],
            }
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

    # Branch H: the additional row is unconfirmed in either order after the exact row matches.
    orig_single = [dict(six_orig[0], transaction_date="2026-10-21", shares=100, price=150.0)]
    amend_155 = dict(orig_single[0], form="4/A", accession="0001-26-000091", price=155.0)
    amend_150 = dict(orig_single[0], form="4/A", accession="0001-26-000091", price=150.0)
    for ordered in ([amend_155, amend_150], [amend_150, amend_155]):
        single_check = _run_us(orig_single + ordered)["checks"]["insider_trades"]
        assert single_check["sales_90d"] == 1
        assert single_check["count_uncertain"] is True
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
    # Without row-level footnotes or pigeonhole proof, mixed filing-level remarks stay conservative (no speculative +1).
    assert res_mixed["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_mixed["checks"]["insider_trades"]["count_uncertain"] is True
    assert not any(f["category"] == "insider" for f in res_mixed["flags"])

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

    # 6. A filing total cannot promote an exact restatement or an unlinked row in a mixed filing.
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
    assert _screen(orig_rows + [two_omit_r1, two_omit_r2])["checks"]["insider_trades"]["sales_90d"] == 3
    two_omit_r3 = dict(two_omit_r2, shares=275, price=175.0)
    res_two_omit_ambig = _screen(orig_rows + [two_omit_r1, two_omit_r2, two_omit_r3])
    assert res_two_omit_ambig["checks"]["insider_trades"]["sales_90d"] == 3
    assert res_two_omit_ambig["checks"]["insider_trades"]["count_uncertain"] is True

    # Single-row 4/A with pure non-sell remarks ("omitted gift") must NOT promote a sell row into a new sale
    gift_only_remark_sell = dict(
        orig_rows[0],
        form="4/A",
        accession="0000320193-26-000319",
        filing_date="2026-10-03",
        shares=275,
        price=175.0,
        footnotes={},
        footnote_refs={},
        remarks="Amending to report an omitted gift.",
    )
    res_gift_remark = _screen(orig_rows + [gift_only_remark_sell])
    assert res_gift_remark["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_gift_remark["checks"]["insider_trades"]["count_uncertain"] is True
    assert not any(f["category"] == "insider" for f in res_gift_remark["flags"])

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

    # 11. Gift recipient correction that merely mentions a sale must NOT reclassify the same-day sale or drop veto
    gift_recip_note = dict(
        corr_sale_priced,
        price=None,
        footnotes={"F1": "Corrects the gift recipient. The gift was reported alongside a sale."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_gift_recip = _screen(six_sales + [same_day_gift, gift_recip_note])
    assert res_gift_recip["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_gift_recip["veto_buy"] is True
    assert res_gift_recip["checks"]["insider_trades"]["count_uncertain"] is False

    # 12. Price correction on active $20 sale to $10 must NOT restore a previously reclassified $10 gift
    pair_10_20_plus_4 = [
        dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=10.0),
        dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=20.0),
    ] + [dict(orig_rows[0], transaction_date=f"2026-10-1{i}", shares=100, price=150.0) for i in range(3, 7)]
    reclass_10_g = dict(
        pair_10_20_plus_4[0],
        form="4/A",
        accession="0000320193-26-000330",
        filing_date="2026-10-18",
        transaction_code="G",
        direction="other",
    )
    corr_20_to_10 = dict(
        pair_10_20_plus_4[1],
        form="4/A",
        accession="0000320193-26-000331",
        filing_date="2026-10-19",
        price=10.0,
        footnotes={"F1": "Corrects sale price from $20 to $10."},
        footnote_refs={"transactionAmounts/transactionPricePerShare/value": ["F1"]},
    )
    res_no_false_restore = _screen(pair_10_20_plus_4 + [reclass_10_g, corr_20_to_10])
    assert res_no_false_restore["checks"]["insider_trades"]["sales_90d"] == 5
    assert res_no_false_restore["veto_buy"] is False

    # 12b. With a 7th sale on another date (6 confirmed sales remain after $10 becomes a gift), uniquely identified
    #      price correction ("from $20 to $10") must preserve confirmed buy veto & count_uncertain=False.
    pair_10_20_plus_5 = pair_10_20_plus_4 + [dict(orig_rows[0], transaction_date="2026-10-17", shares=100, price=150.0)]
    res_preserve_veto = _screen(pair_10_20_plus_5 + [reclass_10_g, corr_20_to_10])
    assert res_preserve_veto["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_preserve_veto["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_preserve_veto["veto_buy"] is True

    # 13. Linked transaction-date correction from Aug 3 to Jul 31 (before Aug 1 cutoff) excludes corrected sale
    aug_sales = [
        dict(orig_rows[0], accession="0000320193-26-000340", filing_date="2026-08-06", transaction_date="2026-08-05"),
        dict(orig_rows[0], accession="0000320193-26-000340", filing_date="2026-08-06", transaction_date="2026-08-04"),
        dict(orig_rows[0], accession="0000320193-26-000341", filing_date="2026-08-05", transaction_date="2026-08-03"),
    ]
    corr_date_to_jul31 = dict(
        aug_sales[2],
        form="4/A",
        accession="0000320193-26-000342",
        filing_date="2026-08-07",
        original_filing_date="2026-08-05",
        transaction_date="2026-07-31",
        footnotes={"F1": "Corrects transaction date from 2026-08-03 to 2026-07-31."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_date_corr = _screen(aug_sales + [corr_date_to_jul31], since_filing_date="2026-08-01")
    assert res_date_corr["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_date_corr["flags"])

    # 14. Two original filings on the SAME filing_date (Aug 6) with distinct transaction dates (Aug 3 vs Aug 4):
    #     stated prior date Aug 3 disambiguates the filing and excludes the corrected Jul 31 sale (2 sales, no flag).
    same_fdate_sales = [
        dict(orig_rows[0], accession="0000320193-26-000350", filing_date="2026-08-06", transaction_date="2026-08-03"),
        dict(orig_rows[0], accession="0000320193-26-000351", filing_date="2026-08-06", transaction_date="2026-08-04"),
        dict(orig_rows[0], accession="0000320193-26-000352", filing_date="2026-08-07", transaction_date="2026-08-05"),
    ]
    corr_same_fdate = dict(
        same_fdate_sales[0],
        form="4/A",
        accession="0000320193-26-000353",
        filing_date="2026-08-08",
        original_filing_date="2026-08-06",
        transaction_date="2026-07-31",
        footnotes={"F1": "Corrects transaction date from 2026-08-03 to 2026-07-31."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_same_fdate_disambig = _screen(same_fdate_sales + [corr_same_fdate], since_filing_date="2026-08-01")
    assert res_same_fdate_disambig["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_same_fdate_disambig["flags"])

    # 15. Unmatched stated prior date (Aug 3 already reclassified to G) must NOT substitute Aug 4 sale!
    aug3_aug4_plus_5 = [
        dict(orig_rows[0], filing_date="2026-08-10", transaction_date="2026-08-03", shares=100, price=10.0),
        dict(orig_rows[0], filing_date="2026-08-10", transaction_date="2026-08-04", shares=100, price=10.0),
    ] + [
        dict(orig_rows[0], filing_date="2026-08-10", transaction_date=f"2026-08-0{i}", shares=100, price=10.0)
        for i in range(5, 10)
    ]
    aug3_to_gift = dict(
        aug3_aug4_plus_5[0],
        form="4/A",
        accession="0000320193-26-000360",
        filing_date="2026-08-11",
        transaction_code="G",
        direction="other",
    )
    aug3_date_corr_no_restore = dict(
        aug3_aug4_plus_5[0],
        form="4/A",
        accession="0000320193-26-000361",
        filing_date="2026-08-12",
        transaction_date="2026-07-31",
        footnotes={"F1": "Corrects transaction date from 2026-08-03 to 2026-07-31."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_no_sub = _screen(aug3_aug4_plus_5 + [aug3_to_gift, aug3_date_corr_no_restore], since_filing_date="2026-08-01")
    assert res_no_sub["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_no_sub["checks"]["insider_trades"]["count_uncertain"] is True

    # 16. Order-independent batch date corrections within one accession (both row orders give identical result)
    batch_orig = [
        dict(
            orig_rows[0],
            accession="0000320193-26-000370",
            filing_date="2026-08-05",
            transaction_date="2026-08-03",
            shares=100,
            price=10.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000370",
            filing_date="2026-08-05",
            transaction_date="2026-08-03",
            shares=200,
            price=20.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000371",
            filing_date="2026-08-06",
            transaction_date="2026-08-05",
            shares=50,
            price=15.0,
        ),
    ]
    batch_row1 = dict(
        batch_orig[0],
        form="4/A",
        accession="0000320193-26-000372",
        filing_date="2026-08-07",
        original_filing_date="2026-08-05",
        transaction_date="2026-08-04",
        shares=100,
        price=10.0,
        footnotes={"F1": "Corrects transaction date from 2026-08-03 to 2026-08-04."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    batch_row2 = dict(
        batch_orig[1],
        form="4/A",
        accession="0000320193-26-000372",
        filing_date="2026-08-07",
        original_filing_date="2026-08-05",
        transaction_date="2026-07-31",
        shares=300,
        price=30.0,
        footnotes={"F2": "Corrects 200 shares at $20 on 2026-08-03 to 2026-07-31 (300 shares at $30)."},
        footnote_refs={"transactionDate/value": ["F2"]},
    )
    res_batch_12 = _screen(batch_orig + [batch_row1, batch_row2], since_filing_date="2026-08-01")
    res_batch_21 = _screen(batch_orig + [batch_row2, batch_row1], since_filing_date="2026-08-01")
    assert res_batch_12["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_batch_21["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_batch_12["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_batch_21["checks"]["insider_trades"]["count_uncertain"] is False
    # Even when row 2 omits old shares/price, both row orders still produce identical counts and uncertainty flags
    batch_row2_bare = dict(
        batch_row2,
        footnotes={"F2": "Corrects transaction date from 2026-08-03 to 2026-07-31."},
    )
    res_bare_12 = _screen(batch_orig + [batch_row1, batch_row2_bare], since_filing_date="2026-08-01")
    res_bare_21 = _screen(batch_orig + [batch_row2_bare, batch_row1], since_filing_date="2026-08-01")
    assert res_bare_12["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_bare_21["checks"]["insider_trades"]["sales_90d"] == 2
    assert (
        res_bare_12["checks"]["insider_trades"]["count_uncertain"]
        == res_bare_21["checks"]["insider_trades"]["count_uncertain"]
        is True
    )

    # 17. Thousands-separated prior price ("from $1,250 to $1,200") preserves confirmed 6-sale buy veto
    six_high_px = [dict(orig_rows[0], transaction_date=f"2026-10-0{i}", shares=100, price=1250.0) for i in range(1, 7)]
    corr_grouped_px = dict(
        six_high_px[0],
        form="4/A",
        accession="0000320193-26-000380",
        filing_date="2026-10-10",
        price=1200.0,
        footnotes={"F1": "Corrects sale price from $1,250 to $1,200."},
        footnote_refs={"transactionAmounts/transactionPricePerShare/value": ["F1"]},
    )
    res_grouped_px = _screen(six_high_px + [corr_grouped_px])
    assert res_grouped_px["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_grouped_px["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_grouped_px["veto_buy"] is True

    # 18. Same-day multi-accession filings on Aug 6 (both Aug 3 sales: 100@$10 vs 200@$20) disambiguated by old shares/price
    same_day_two_acc = [
        dict(
            orig_rows[0],
            accession="0000320193-26-000381",
            filing_date="2026-08-06",
            transaction_date="2026-08-03",
            shares=100,
            price=10.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000382",
            filing_date="2026-08-06",
            transaction_date="2026-08-03",
            shares=200,
            price=20.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000383",
            filing_date="2026-08-07",
            transaction_date="2026-08-05",
            shares=50,
            price=15.0,
        ),
    ]
    move_200_jul31 = dict(
        same_day_two_acc[1],
        form="4/A",
        accession="0000320193-26-000384",
        filing_date="2026-08-08",
        original_filing_date="2026-08-06",
        transaction_date="2026-07-31",
        shares=300,
        price=30.0,
        footnotes={"F1": "Corrects 200 shares at $20 on 2026-08-03 to 2026-07-31 (300 shares at $30)."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_two_acc_disambig = _screen(same_day_two_acc + [move_200_jul31], since_filing_date="2026-08-01")
    assert res_two_acc_disambig["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_two_acc_disambig["flags"])

    # 19. Explicit share+price change (200@$20 -> 100@$30) alongside same-day 100@$10 + 4 others preserves buy veto
    six_share_change = [
        dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=10.0),
        dict(orig_rows[0], transaction_date="2026-10-12", shares=200, price=20.0),
    ] + [dict(orig_rows[0], transaction_date=f"2026-10-1{i}", shares=100, price=150.0) for i in range(3, 7)]
    corr_200_to_100 = dict(
        six_share_change[1],
        form="4/A",
        accession="0000320193-26-000385",
        filing_date="2026-10-18",
        shares=100,
        price=30.0,
        footnotes={"F1": "Corrects previously reported 200 shares at $20 to 100 shares at $30."},
        footnote_refs={"transactionAmounts/transactionShares/value": ["F1"]},
    )
    res_share_change_veto = _screen(six_share_change + [corr_200_to_100])
    assert res_share_change_veto["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_share_change_veto["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_share_change_veto["veto_buy"] is True

    # 20. Indistinguishable original $10 sales across two filings with $11/$12 amendments stay uncertain & order-independent
    sym_orig = [
        dict(
            orig_rows[0],
            accession="0000320193-26-000390",
            filing_date="2026-08-05",
            transaction_date="2026-08-03",
            shares=100,
            price=10.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000391",
            filing_date="2026-08-06",
            transaction_date="2026-08-03",
            shares=100,
            price=10.0,
        ),
    ]
    sym_a11 = dict(sym_orig[0], form="4/A", accession="0000320193-26-000392", filing_date="2026-08-07", price=11.0)
    sym_a12 = dict(sym_orig[0], form="4/A", accession="0000320193-26-000392", filing_date="2026-08-07", price=12.0)
    sym_move_f1 = dict(
        sym_orig[0],
        form="4/A",
        accession="0000320193-26-000393",
        filing_date="2026-08-08",
        original_filing_date="2026-08-05",
        transaction_date="2026-07-31",
        price=13.0,
        footnotes={"F1": "Corrects transaction date from 2026-08-03 (previously at $11) to 2026-07-31 at $13."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_sym_ab = _screen(sym_orig + [sym_a11, sym_a12, sym_move_f1], since_filing_date="2026-08-01")
    res_sym_ba = _screen(sym_orig + [sym_a12, sym_a11, sym_move_f1], since_filing_date="2026-08-01")
    assert res_sym_ab["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_sym_ba["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_sym_ab["checks"]["insider_trades"]["count_uncertain"] is True
    assert res_sym_ba["checks"]["insider_trades"]["count_uncertain"] is True

    # 21. Separate unchanged sale price mentioned in footnote ("executed at $1,250 is unchanged") must NOT confuse prior price
    six_sep_px = [
        dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=20.0),
        dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=1250.0),
    ] + [dict(orig_rows[0], transaction_date=f"2026-10-1{i}", shares=100, price=150.0) for i in range(3, 7)]
    corr_sep_px = dict(
        six_sep_px[0],
        form="4/A",
        accession="0000320193-26-000394",
        filing_date="2026-10-18",
        price=21.0,
        footnotes={"F1": "Corrects sale price from $20 to $21. A separate sale executed at $1,250 is unchanged."},
        footnote_refs={"transactionAmounts/transactionPricePerShare/value": ["F1"]},
    )
    res_sep_px = _screen(six_sep_px + [corr_sep_px])
    assert res_sep_px["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_sep_px["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_sep_px["veto_buy"] is True

    # 22. Competing exact matches with historical alias ($10 -> $11, then accession restating both $10 and $11 in either order)
    six_alias = [
        dict(
            orig_rows[0],
            accession="0000320193-26-000400",
            filing_date="2026-10-02",
            transaction_date="2026-10-01",
            shares=100,
            price=10.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000401",
            filing_date="2026-10-03",
            transaction_date="2026-10-01",
            shares=100,
            price=10.0,
        ),
    ] + [
        dict(
            orig_rows[0],
            accession="0000320193-26-000402",
            filing_date="2026-10-04",
            transaction_date=f"2026-10-0{i}",
            shares=100,
            price=150.0,
        )
        for i in range(2, 6)
    ]
    corr_alias_11 = dict(
        six_alias[0],
        form="4/A",
        accession="0000320193-26-000403",
        filing_date="2026-10-06",
        original_filing_date="2026-10-02",
        price=11.0,
        footnotes={"F1": "Corrects sale price from $10 to $11."},
        footnote_refs={"transactionAmounts/transactionPricePerShare/value": ["F1"]},
    )
    restate_10 = dict(six_alias[1], form="4/A", accession="0000320193-26-000404", filing_date="2026-10-07", price=10.0)
    restate_11 = dict(six_alias[0], form="4/A", accession="0000320193-26-000404", filing_date="2026-10-07", price=11.0)
    for ordered in ([restate_10, restate_11], [restate_11, restate_10]):
        res_alias = _screen(six_alias + [corr_alias_11] + ordered)
        assert res_alias["checks"]["insider_trades"]["sales_90d"] == 6
        assert res_alias["checks"]["insider_trades"]["count_uncertain"] is False
        assert res_alias["veto_buy"] is True

    # 23. Explicit date correction takes priority over unannotated share restatement in both row orders
    prio_orig = [
        dict(
            orig_rows[0],
            accession="0000320193-26-000410",
            filing_date="2026-08-05",
            transaction_date="2026-08-03",
            shares=100,
            price=10.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000410",
            filing_date="2026-08-05",
            transaction_date="2026-08-03",
            shares=200,
            price=20.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000411",
            filing_date="2026-08-06",
            transaction_date="2026-08-05",
            shares=50,
            price=15.0,
        ),
    ]
    prio_unannot = dict(
        prio_orig[0], form="4/A", accession="0000320193-26-000412", filing_date="2026-08-07", price=15.0
    )
    prio_date_corr = dict(
        prio_orig[0],
        form="4/A",
        accession="0000320193-26-000412",
        filing_date="2026-08-07",
        transaction_date="2026-07-31",
        footnotes={"F1": "Corrects transaction date from 2026-08-03 to 2026-07-31."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    prio_reclass_200 = dict(
        prio_orig[1],
        form="4/A",
        accession="0000320193-26-000413",
        filing_date="2026-08-08",
        transaction_code="G",
        direction="other",
    )
    for ordered in ([prio_unannot, prio_date_corr], [prio_date_corr, prio_unannot]):
        res_prio = _screen(prio_orig + ordered, since_filing_date="2026-08-01")
        assert res_prio["checks"]["insider_trades"]["sales_90d"] == 2
        assert res_prio["checks"]["insider_trades"]["count_uncertain"] is True
        # Follow-up reclassification of the 200-share sale to G drops count to 1, proving the 100-share sale moved.
        res_prio_after_g = _screen(prio_orig + ordered + [prio_reclass_200], since_filing_date="2026-08-01")
        assert res_prio_after_g["checks"]["insider_trades"]["sales_90d"] == 1

    # 24. Match stated prior shares and price against the SAME transaction revision (never mix rev 1 shares + rev 2 price)
    six_rev = [
        dict(
            orig_rows[0],
            accession="0000320193-26-000420",
            filing_date="2026-10-02",
            transaction_date="2026-10-01",
            shares=200,
            price=20.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000421",
            filing_date="2026-10-03",
            transaction_date="2026-10-01",
            shares=200,
            price=30.0,
        ),
    ] + [
        dict(
            orig_rows[0],
            accession="0000320193-26-000422",
            filing_date="2026-10-04",
            transaction_date=f"2026-10-0{i}",
            shares=100,
            price=150.0,
        )
        for i in range(2, 6)
    ]
    rev_first = dict(
        six_rev[0],
        form="4/A",
        accession="0000320193-26-000423",
        filing_date="2026-10-06",
        original_filing_date="2026-10-02",
        shares=100,
        price=30.0,
        footnotes={"F1": "Corrects previously reported 200 shares at $20 to 100 shares at $30."},
        footnote_refs={"transactionAmounts/transactionShares/value": ["F1"]},
    )
    rev_second = dict(
        six_rev[1],
        form="4/A",
        accession="0000320193-26-000424",
        filing_date="2026-10-07",
        shares=300,
        price=40.0,
        footnotes={"F1": "Corrects previously reported 200 shares at $30 to 300 shares at $40."},
        footnote_refs={"transactionAmounts/transactionShares/value": ["F1"]},
    )
    res_same_rev = _screen(six_rev + [rev_first, rev_second])
    assert res_same_rev["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_same_rev["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_same_rev["veto_buy"] is True

    # 25. Fractional prior prices ("from $20.50 to $21.75") must NOT be split at decimal points
    six_frac_px = [
        dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=20.50),
        dict(orig_rows[0], transaction_date="2026-10-12", shares=100, price=1250.50),
    ] + [dict(orig_rows[0], transaction_date=f"2026-10-1{i}", shares=100, price=150.0) for i in range(3, 7)]
    corr_frac_px = dict(
        six_frac_px[0],
        form="4/A",
        accession="0000320193-26-000430",
        filing_date="2026-10-18",
        price=21.75,
        footnotes={
            "F1": "Corrects sale price from $20.50 to $21.75. A separate sale executed at $1,250.50 is unchanged."
        },
        footnote_refs={"transactionAmounts/transactionPricePerShare/value": ["F1"]},
    )
    res_frac_px = _screen(six_frac_px + [corr_frac_px])
    assert res_frac_px["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_frac_px["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_frac_px["veto_buy"] is True

    # 26. Unannotated historical restatement of Aug 3 after linked date correction to Jul 31 must NOT revert to Aug 3
    hist_restate_aug3 = dict(
        aug_sales[2],
        form="4/A",
        accession="0000320193-26-000431",
        filing_date="2026-08-09",
    )
    res_hist_no_revert = _screen(aug_sales + [corr_date_to_jul31, hist_restate_aug3], since_filing_date="2026-08-01")
    assert res_hist_no_revert["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_hist_no_revert["flags"])

    # 27. Overlapping symmetric exact-match groups (A, B in filing 1 + C in filing 2; 2 scoped + 1 unscoped in any order)
    overlap_orig = [
        dict(orig_rows[0], accession="0000320193-26-000440", filing_date="2026-10-02", shares=100, price=10.0),
        dict(orig_rows[0], accession="0000320193-26-000440", filing_date="2026-10-02", shares=100, price=10.0),
        dict(orig_rows[0], accession="0000320193-26-000441", filing_date="2026-10-03", shares=100, price=10.0),
    ]
    scoped_r1 = dict(
        overlap_orig[0],
        form="4/A",
        accession="0000320193-26-000442",
        filing_date="2026-10-05",
        original_filing_date="2026-10-02",
    )
    scoped_r2 = dict(scoped_r1)
    unscoped_r3 = dict(
        overlap_orig[2],
        form="4/A",
        accession="0000320193-26-000442",
        filing_date="2026-10-05",
        original_filing_date=None,
    )
    for ordered in ([unscoped_r3, scoped_r1, scoped_r2], [scoped_r1, scoped_r2, unscoped_r3]):
        res_overlap = _screen(overlap_orig + ordered)
        assert res_overlap["checks"]["insider_trades"]["sales_90d"] == 3
        assert res_overlap["checks"]["insider_trades"]["count_uncertain"] is False

    # 28. Symmetric exact restatements and historical restatements preserve filing_date provenance for later amendments
    prov_orig = [
        dict(
            orig_rows[0],
            accession="0000320193-26-000450",
            filing_date="2026-08-05",
            transaction_date="2026-08-04",
            shares=100,
            price=10.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000450",
            filing_date="2026-08-05",
            transaction_date="2026-08-04",
            shares=100,
            price=10.0,
        ),
        dict(
            orig_rows[0],
            accession="0000320193-26-000451",
            filing_date="2026-08-06",
            transaction_date="2026-08-05",
            shares=50,
            price=15.0,
        ),
    ]
    prov_restate_1 = dict(prov_orig[0], form="4/A", accession="0000320193-26-000452", filing_date="2026-08-07")
    prov_restate_2 = dict(prov_restate_1)
    prov_gift_link = dict(
        prov_orig[0],
        form="4/A",
        accession="0000320193-26-000453",
        filing_date="2026-08-08",
        original_filing_date="2026-08-07",
        transaction_code="G",
        direction="other",
        footnotes={"F1": "Corrects previously misreported sale to a gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_prov = _screen(prov_orig + [prov_restate_1, prov_restate_2, prov_gift_link], since_filing_date="2026-08-01")
    assert res_prov["checks"]["insider_trades"]["sales_90d"] == 2
    assert not any(f["category"] == "insider" for f in res_prov["flags"])

    # Historical restatement on Aug 9 preserves Aug 9 provenance so an Aug 10 correction linked to Aug 9 still resolves
    hist_link_followup = dict(
        corr_date_to_jul31,
        accession="0000320193-26-000432",
        filing_date="2026-08-10",
        original_filing_date="2026-08-09",
        transaction_date="2026-08-04",
        footnotes={"F1": "Corrects transaction date from 2026-07-31 to 2026-08-04."},
        footnote_refs={"transactionDate/value": ["F1"]},
    )
    res_hist_prov = _screen(
        aug_sales + [corr_date_to_jul31, hist_restate_aug3, hist_link_followup], since_filing_date="2026-08-01"
    )
    assert res_hist_prov["checks"]["insider_trades"]["sales_90d"] == 3
    assert res_hist_prov["checks"]["insider_trades"]["count_uncertain"] is False

    # 29. Single unscoped restatement matching two identical sales across different filings must NOT attach provenance
    #     to an arbitrary single slot: both slot orderings [A, B] and [B, A] produce identical count and uncertainty.
    ambig_a = dict(
        orig_rows[0],
        accession="0000320193-26-000460",
        filing_date="2026-08-04",
        transaction_date="2026-08-03",
        shares=100,
        price=10.0,
    )
    ambig_b = dict(
        orig_rows[0],
        accession="0000320193-26-000461",
        filing_date="2026-08-05",
        transaction_date="2026-08-03",
        shares=100,
        price=10.0,
    )
    ambig_others = [
        dict(
            orig_rows[0],
            accession="0000320193-26-000462",
            filing_date="2026-08-06",
            transaction_date=f"2026-08-0{i}",
            shares=50,
            price=15.0,
        )
        for i in range(4, 9)
    ]
    ambig_unscoped_restate = dict(
        ambig_a,
        form="4/A",
        accession="0000320193-26-000463",
        filing_date="2026-08-09",
        original_filing_date=None,
    )
    ambig_gift_a = dict(
        ambig_a,
        form="4/A",
        accession="0000320193-26-000464",
        filing_date="2026-08-10",
        original_filing_date="2026-08-04",
        transaction_code="G",
        direction="other",
    )
    ambig_gift_restate = dict(
        ambig_a,
        form="4/A",
        accession="0000320193-26-000465",
        filing_date="2026-08-11",
        original_filing_date="2026-08-09",
        transaction_code="G",
        direction="other",
    )
    res_ambig_ab = _screen(
        [ambig_a, ambig_b] + ambig_others + [ambig_unscoped_restate, ambig_gift_a, ambig_gift_restate],
        since_filing_date="2026-08-01",
    )
    res_ambig_ba = _screen(
        [ambig_b, ambig_a] + ambig_others + [ambig_unscoped_restate, ambig_gift_a, ambig_gift_restate],
        since_filing_date="2026-08-01",
    )
    assert res_ambig_ab["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_ambig_ba["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_ambig_ab["checks"]["insider_trades"]["count_uncertain"] is True
    assert res_ambig_ba["checks"]["insider_trades"]["count_uncertain"] is True
    assert res_ambig_ab["veto_buy"] is False
    assert res_ambig_ba["veto_buy"] is False

    # 30. Unattributable filing-level remarks (plural nouns, prior-filing counts, omitted-gift remarks, or
    #     cross-cutoff candidates) must NEVER fabricate speculative sale additions or trigger medium-risk flags.
    cap_orig = {
        "form": "4",
        "accession": "0001-26-000501",
        "filing_date": "2026-10-05",
        "transaction_date": "2026-10-04",
        "transaction_code": "S",
        "owner": "Cap Owner",
        "owners": [{"cik": "0000000030", "name": "Cap Owner"}],
        "security": "Common Stock",
        "derivative": False,
        "shares": 100.0,
        "price": 100.0,
    }
    for rem_text in (
        "Amending to correct one transaction and add an omitted transaction.",
        "Amending to correct one transaction and add one of the omitted sales.",
        "Amending to correct one transaction and report one omitted sale. The original filing reported six transactions.",
    ):
        cap_in = {
            "form": "4/A",
            "accession": "0001-26-000502",
            "filing_date": "2026-10-06",
            "original_filing_date": "2026-10-05",
            "transaction_date": "2026-10-04",
            "transaction_code": "S",
            "owner": "Cap Owner",
            "owners": [{"cik": "0000000030", "name": "Cap Owner"}],
            "security": "Common Stock",
            "derivative": False,
            "shares": 120.0,
            "price": 105.0,
            "remarks": rem_text,
        }
        cap_out = {
            "form": "4/A",
            "accession": "0001-26-000502",
            "filing_date": "2026-10-06",
            "original_filing_date": "2026-10-05",
            "transaction_date": "2026-09-20",
            "transaction_code": "S",
            "owner": "Cap Owner",
            "owners": [{"cik": "0000000030", "name": "Cap Owner"}],
            "security": "Common Stock",
            "derivative": False,
            "shares": 250.0,
            "price": None,
            "remarks": rem_text,
        }
        res_cap_12 = _screen([cap_orig, cap_in, cap_out], since_filing_date="2026-10-01")
        res_cap_21 = _screen([cap_orig, cap_out, cap_in], since_filing_date="2026-10-01")
        assert res_cap_12["checks"]["insider_trades"]["sales_90d"] == 1
        assert res_cap_21["checks"]["insider_trades"]["sales_90d"] == 1
        assert res_cap_12["checks"]["insider_trades"]["count_uncertain"] is True
        assert res_cap_21["checks"]["insider_trades"]["count_uncertain"] is True
        assert not any(f["category"] == "insider" for f in res_cap_12["flags"])
        assert not any(f["category"] == "insider" for f in res_cap_21["flags"])

    # 30b. Omitted gift + corrected sale remarks must NOT inflate 2 original sales into 3
    two_cap_orig = [
        cap_orig,
        dict(cap_orig, transaction_date="2026-10-05", shares=150.0, price=110.0),
    ]
    corr_unlinked_sale = dict(
        cap_orig,
        form="4/A",
        accession="0001-26-000503",
        filing_date="2026-10-06",
        original_filing_date="2026-10-05",
        shares=125.0,
        price=108.0,
        remarks="Corrects one sale and reports one omitted gift.",
    )
    omitted_gift_row = dict(
        cap_orig,
        form="4/A",
        accession="0001-26-000503",
        filing_date="2026-10-06",
        original_filing_date="2026-10-05",
        transaction_date="2026-10-06",
        transaction_code="G",
        direction="other",
        shares=50.0,
        price=0.0,
        remarks="Corrects one sale and reports one omitted gift.",
    )
    res_omit_gift = _screen(two_cap_orig + [corr_unlinked_sale, omitted_gift_row], since_filing_date="2026-10-01")
    assert res_omit_gift["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_omit_gift["checks"]["insider_trades"]["count_uncertain"] is True
    assert not any(f["category"] == "insider" for f in res_omit_gift["flags"])

    # 30c. Cross-cutoff unlinked candidates (one outside 2026-08-28 cutoff, one inside) must NOT bias toward in-window
    cut_orig = [
        dict(cap_orig, filing_date="2026-11-21", transaction_date="2026-11-18", shares=100.0, price=100.0),
        dict(cap_orig, filing_date="2026-11-21", transaction_date="2026-11-20", shares=200.0, price=110.0),
    ]
    cut_rem = "Amending to report an omitted 2026-08-10 sale and correct the 2026-11-20 sale."
    cut_out_row = dict(
        cut_orig[0],
        form="4/A",
        accession="0001-26-000504",
        filing_date="2026-11-22",
        original_filing_date="2026-11-21",
        transaction_date="2026-08-10",
        shares=90.0,
        price=95.0,
        remarks=cut_rem,
    )
    cut_in_row = dict(
        cut_orig[1],
        form="4/A",
        accession="0001-26-000504",
        filing_date="2026-11-22",
        original_filing_date="2026-11-21",
        transaction_date="2026-11-20",
        shares=220.0,
        price=115.0,
        remarks=cut_rem,
    )
    for ordered in ([cut_out_row, cut_in_row], [cut_in_row, cut_out_row]):
        res_cut = _screen(cut_orig + ordered, since_filing_date="2026-08-28")
        assert res_cut["checks"]["insider_trades"]["sales_90d"] == 2
        assert res_cut["checks"]["insider_trades"]["count_uncertain"] is True
        assert not any(f["category"] == "insider" for f in res_cut["flags"])

    # 31. Consecutive date corrections where the second 4/A references the original transaction date (10-06 -> 09-29)
    #     after an intermediate 4/A moved it to 10-07 must still match the prior revision and exclude the sale.
    seq_orig = {
        "form": "4",
        "accession": "0001-26-000511",
        "filing_date": "2026-10-06",
        "transaction_date": "2026-10-06",
        "transaction_code": "S",
        "owner": "Seq Date Owner",
        "owners": [{"cik": "0000000031", "name": "Seq Date Owner"}],
        "security": "Common Stock",
        "derivative": False,
        "shares": 100.0,
        "price": 50.0,
    }
    seq_amend_1 = {
        "form": "4/A",
        "accession": "0001-26-000512",
        "filing_date": "2026-10-07",
        "original_filing_date": "2026-10-06",
        "transaction_date": "2026-10-07",
        "transaction_code": "S",
        "owner": "Seq Date Owner",
        "owners": [{"cik": "0000000031", "name": "Seq Date Owner"}],
        "security": "Common Stock",
        "derivative": False,
        "shares": 100.0,
        "price": 50.0,
        "footnotes": {"F1": "Corrects transaction date from 2026-10-06 to 2026-10-07."},
        "footnote_refs": {"transactionDate": ["F1"]},
    }
    seq_amend_2 = {
        "form": "4/A",
        "accession": "0001-26-000513",
        "filing_date": "2026-10-08",
        "original_filing_date": "2026-10-06",
        "transaction_date": "2026-09-29",
        "transaction_code": "S",
        "owner": "Seq Date Owner",
        "owners": [{"cik": "0000000031", "name": "Seq Date Owner"}],
        "security": "Common Stock",
        "derivative": False,
        "shares": 100.0,
        "price": 50.0,
        "footnotes": {"F1": "Corrects transaction date originally reported as 2026-10-06 to 2026-09-29."},
        "footnote_refs": {"transactionDate": ["F1"]},
    }
    res_seq_only2 = _screen([seq_orig, seq_amend_2], since_filing_date="2026-10-01")
    res_seq_both = _screen([seq_orig, seq_amend_1, seq_amend_2], since_filing_date="2026-10-01")
    assert res_seq_only2["checks"]["insider_trades"]["sales_90d"] == 0
    assert res_seq_both["checks"]["insider_trades"]["sales_90d"] == 0
    assert res_seq_both["checks"]["insider_trades"]["count_uncertain"] is False
    assert not any(f["category"] == "insider" for f in res_seq_both["flags"])

    # 32. Same-day amendments from different submitting agents must execute in EDGAR acceptanceDateTime order
    #     (14:00Z prefix 999 addition before 16:00Z prefix 111 gift reclassification), and flag uncertainty when
    #     acceptance_datetime is missing so cross-submitter order cannot be determined.
    same_day_orig = [
        {
            "form": "4",
            "accession": "0000555555-26-000001",
            "filing_date": "2026-10-03",
            "transaction_date": f"2026-10-0{i}",
            "transaction_code": "S",
            "owner": "Same Day Owner",
            "owners": [{"cik": "0000000032", "name": "Same Day Owner"}],
            "security": "Common Stock",
            "derivative": False,
            "shares": 100.0 + i,
            "price": 50.0,
        }
        for i in (1, 2)
    ]
    same_day_add = {
        "form": "4/A",
        "accession": "0000999999-26-000001",
        "filing_date": "2026-10-05",
        "acceptance_datetime": "2026-10-05T14:00:00.000Z",
        "original_filing_date": "2026-10-03",
        "transaction_date": "2026-10-03",
        "transaction_code": "S",
        "owner": "Same Day Owner",
        "owners": [{"cik": "0000000032", "name": "Same Day Owner"}],
        "security": "Common Stock",
        "derivative": False,
        "shares": 101.0,
        "price": 51.0,
        "footnotes": {"F1": "Reports an inadvertently omitted sale."},
        "footnote_refs": {"transactionCode": ["F1"]},
    }
    same_day_gift = {
        "form": "4/A",
        "accession": "0000111111-26-000002",
        "filing_date": "2026-10-05",
        "acceptance_datetime": "2026-10-05T16:00:00.000Z",
        "original_filing_date": "2026-10-03",
        "transaction_date": "2026-10-03",
        "transaction_code": "G",
        "owner": "Same Day Owner",
        "owners": [{"cik": "0000000032", "name": "Same Day Owner"}],
        "security": "Common Stock",
        "derivative": False,
        "shares": 101.0,
        "price": 0.0,
        "footnotes": {"F1": "Corrects previously reported sale as a bona fide gift."},
        "footnote_refs": {"transactionCode": ["F1"]},
    }
    res_same_day_ts = _screen(same_day_orig + [same_day_gift, same_day_add], since_filing_date="2026-10-01")
    assert res_same_day_ts["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_same_day_ts["checks"]["insider_trades"]["count_uncertain"] is False
    assert not any(f["category"] == "insider" for f in res_same_day_ts["flags"])

    same_day_add_no_ts = {k: v for k, v in same_day_add.items() if k != "acceptance_datetime"}
    same_day_gift_no_ts = {k: v for k, v in same_day_gift.items() if k != "acceptance_datetime"}
    res_same_day_no_ts = _screen(
        same_day_orig + [same_day_add_no_ts, same_day_gift_no_ts], since_filing_date="2026-10-01"
    )
    assert res_same_day_no_ts["checks"]["insider_trades"]["count_uncertain"] is True

    # 33. Apply row-level evidence (shares/price) BEFORE checking multi-accession ambiguity:
    #     33a (Weaxs): 2 orig same-day sales (100sh, 150sh) + 4/A #1 adds same-day 200sh + 4/A #2 reclassifies 200sh
    #         to Gift G sharing original_filing_date -> 2 sales, no insider flag, count_uncertain=False.
    chain_orig_2 = [
        {
            "form": "4",
            "accession": "0000555555-26-000101",
            "filing_date": "2026-10-03",
            "transaction_date": "2026-10-03",
            "transaction_code": "S",
            "owner": "Chain Owner",
            "owners": [{"cik": "0000000033", "name": "Chain Owner"}],
            "security": "Common Stock",
            "derivative": False,
            "shares": shares,
            "price": 50.0,
        }
        for shares in (100.0, 150.0)
    ]
    chain_add_200 = {
        "form": "4/A",
        "accession": "0000555555-26-000102",
        "filing_date": "2026-10-04",
        "original_filing_date": "2026-10-03",
        "transaction_date": "2026-10-03",
        "transaction_code": "S",
        "owner": "Chain Owner",
        "owners": [{"cik": "0000000033", "name": "Chain Owner"}],
        "security": "Common Stock",
        "derivative": False,
        "shares": 200.0,
        "price": 60.0,
        "footnotes": {"F1": "Reports an inadvertently omitted sale."},
        "footnote_refs": {"transactionCode": ["F1"]},
    }
    chain_gift_200 = {
        "form": "4/A",
        "accession": "0000555555-26-000103",
        "filing_date": "2026-10-05",
        "original_filing_date": "2026-10-03",
        "transaction_date": "2026-10-03",
        "transaction_code": "G",
        "direction": "other",
        "owner": "Chain Owner",
        "owners": [{"cik": "0000000033", "name": "Chain Owner"}],
        "security": "Common Stock",
        "derivative": False,
        "shares": 200.0,
        "price": 60.0,
        "footnotes": {"F1": "Corrects previously misreported 200-share sale to a bona fide gift."},
        "footnote_refs": {"transactionCode": ["F1"]},
    }
    res_chain_200_before = _screen(chain_orig_2 + [chain_add_200], since_filing_date="2026-10-01")
    assert res_chain_200_before["checks"]["insider_trades"]["sales_90d"] == 3
    res_chain_200 = _screen(chain_orig_2 + [chain_add_200, chain_gift_200], since_filing_date="2026-10-01")
    assert res_chain_200["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_chain_200["checks"]["insider_trades"]["count_uncertain"] is False
    assert not any(f["category"] == "insider" for f in res_chain_200["flags"])

    #     33b (CodeRabbit Major): 6 orig sales (incl. same-day 100@$50) + 4/A #1 adds 7th same-day 200@$60 +
    #         4/A #2 reclassifies 200@$60 to Gift G -> 6 confirmed orig sales remain, buy veto preserved!
    chain_orig_6 = [chain_orig_2[0]] + [
        dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=110.0 + i) for i in range(4, 9)
    ]
    res_chain_veto_before = _screen(chain_orig_6 + [chain_add_200], since_filing_date="2026-10-01")
    assert res_chain_veto_before["checks"]["insider_trades"]["sales_90d"] == 7
    res_chain_veto = _screen(chain_orig_6 + [chain_add_200, chain_gift_200], since_filing_date="2026-10-01")
    assert res_chain_veto["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_chain_veto["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_chain_veto["veto_buy"] is True

    #     33c: Multi-accession ambiguity IS still enforced when 2 distinct filings both match exact shares & price
    dup_acc_orig = [
        dict(chain_orig_2[0], accession="0000555555-26-000201", shares=200.0, price=60.0),
        dict(chain_orig_2[0], accession="0000555555-26-000202", shares=200.0, price=60.0),
    ]
    res_dup_acc = _screen(dup_acc_orig + [chain_gift_200], since_filing_date="2026-10-01")
    assert res_dup_acc["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_dup_acc["checks"]["insider_trades"]["count_uncertain"] is True

    # 34. Sentence-ending period after numbers ("at $30. A separate sale was 500 shares") must split cleanly so
    #     unrelated share counts in the next sentence do not make a confirmed six-sale correction ambiguous.
    sent_orig = [
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=200.0, price=20.0),
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=500.0, price=20.0),
    ] + [dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=110.0 + i) for i in range(4, 8)]
    sent_corr = dict(
        sent_orig[0],
        form="4/A",
        accession="0000555555-26-000210",
        filing_date="2026-10-09",
        original_filing_date="2026-10-03",
        shares=300.0,
        price=30.0,
        footnotes={"F1": "Corrects 200 shares at $20 to 300 shares at $30. A separate sale was 500 shares."},
        footnote_refs={"transactionAmounts/transactionShares/value": ["F1"]},
    )
    res_sent = _screen(sent_orig + [sent_corr], since_filing_date="2026-10-01")
    assert res_sent["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_sent["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_sent["veto_buy"] is True

    # 35. Restoring a sale after a share-changing gift (200-sh S -> 120-sh G -> linked restore citing 120 shares)
    #     must match the stored gift revision and preserve the 6-sale buy veto.
    gift_rev_orig = [
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=200.0, price=20.0),
    ] + [dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=110.0 + i) for i in range(4, 9)]
    gift_rev_120 = dict(
        gift_rev_orig[0],
        form="4/A",
        accession="0000555555-26-000220",
        filing_date="2026-10-09",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        shares=120.0,
        price=0.0,
        footnotes={"F1": "Corrects previously misreported 200-share sale to a 120-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    restore_from_120 = dict(
        gift_rev_orig[0],
        form="4/A",
        accession="0000555555-26-000221",
        filing_date="2026-10-10",
        original_filing_date="2026-10-09",
        shares=200.0,
        price=20.0,
        footnotes={"F1": "Restores sale: previously reported as 120 shares gift, should have been a 200-share sale."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_gift_rev = _screen(gift_rev_orig + [gift_rev_120, restore_from_120], since_filing_date="2026-10-01")
    assert res_gift_rev["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_gift_rev["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_gift_rev["veto_buy"] is True

    # 36. An unscoped amendment filed on Oct 5 must NOT match an identical original filed LATER on Oct 8.
    chron_oct4 = dict(
        chain_orig_2[0], accession="0000555555-26-000230", filing_date="2026-10-04", shares=100.0, price=50.0
    )
    chron_oct8 = dict(
        chain_orig_2[0], accession="0000555555-26-000231", filing_date="2026-10-08", shares=100.0, price=50.0
    )
    chron_others = [dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=110.0 + i) for i in range(4, 9)]
    chron_g_oct5 = dict(
        chron_oct4,
        form="4/A",
        accession="0000555555-26-000232",
        filing_date="2026-10-05",
        original_filing_date=None,
        transaction_code="G",
        direction="other",
    )
    chron_g_oct9 = dict(
        chron_oct8,
        form="4/A",
        accession="0000555555-26-000233",
        filing_date="2026-10-09",
        original_filing_date="2026-10-08",
        transaction_code="G",
        direction="other",
    )
    res_chron = _screen(
        [chron_oct8, chron_oct4] + chron_others + [chron_g_oct5, chron_g_oct9], since_filing_date="2026-10-01"
    )
    assert res_chron["checks"]["insider_trades"]["sales_90d"] == 5
    assert res_chron["veto_buy"] is False

    # 37. Gift correction stating prior shares (200-sh -> 100-sh G) must NOT hijack an unrelated same-day 100-sh sale.
    prior_g_orig = [
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=200.0, price=20.0),
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=100.0, price=10.0),
    ] + [dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=110.0 + i) for i in range(4, 9)]
    prior_g_200_to_100 = dict(
        prior_g_orig[0],
        form="4/A",
        accession="0000555555-26-000240",
        filing_date="2026-10-09",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        shares=100.0,
        price=0.0,
        footnotes={"F1": "Corrects previously misreported 200-share sale to a 100-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    prior_g_100 = dict(
        prior_g_orig[1],
        form="4/A",
        accession="0000555555-26-000241",
        filing_date="2026-10-10",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
    )
    res_prior_g = _screen(prior_g_orig + [prior_g_200_to_100, prior_g_100], since_filing_date="2026-10-01")
    assert res_prior_g["checks"]["insider_trades"]["sales_90d"] == 5
    assert res_prior_g["veto_buy"] is False

    # 38. Active sale with missing/invalid transaction_date must mark count_uncertain=True consistently.
    undated_orig = [
        dict(chain_orig_2[0], transaction_date="2026-10-03"),
        dict(chain_orig_2[0], transaction_date="2026-10-04"),
        dict(chain_orig_2[0], transaction_date=None),
    ]
    res_undated = _screen(undated_orig, since_filing_date="2026-10-01")
    assert res_undated["checks"]["insider_trades"]["sales_90d"] == 2
    assert res_undated["checks"]["insider_trades"]["count_uncertain"] is True
    assert not any(f["category"] == "insider" for f in res_undated["flags"])

    # 39. "previously reported alongside a gift" must NOT trigger gift-restoration mode on an active sale restatement.
    alongside_orig = [
        dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=100.0 + i, price=50.0) for i in range(3, 9)
    ]
    alongside_restate = dict(
        alongside_orig[0],
        form="4/A",
        accession="0000555555-26-000250",
        filing_date="2026-10-09",
        original_filing_date="2026-10-03",
        footnotes={"F1": "Corrects the post-transaction balance. This sale was previously reported alongside a gift."},
        footnote_refs={"postTransactionAmounts": ["F1"]},
    )
    res_alongside = _screen(alongside_orig + [alongside_restate], since_filing_date="2026-10-01")
    assert res_alongside["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_alongside["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_alongside["veto_buy"] is True

    # 40. Same-accession swap (Sale A -> 120-sh gift AND restore prior 120-sh Gift B -> sale) must match against
    #     pre-accession revisions so A's new gift revision never collides with B's restoration.
    swap_orig = [
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=200.0, price=20.0),
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=300.0, price=30.0),
    ] + [dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=110.0 + i) for i in range(4, 9)]
    swap_b_to_gift = dict(
        swap_orig[1],
        form="4/A",
        accession="0000555555-26-000260",
        filing_date="2026-10-09",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        shares=120.0,
        price=0.0,
        footnotes={"F1": "Corrects previously misreported 300-share sale to a 120-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    swap_a_to_gift = dict(
        swap_orig[0],
        form="4/A",
        accession="0000555555-26-000261",
        filing_date="2026-10-10",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        shares=120.0,
        price=0.0,
        footnotes={"F1": "Corrects previously misreported 200-share sale to a 120-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    swap_restore_b = dict(
        swap_orig[1],
        form="4/A",
        accession="0000555555-26-000261",
        filing_date="2026-10-10",
        original_filing_date="2026-10-09",
        shares=300.0,
        price=30.0,
        footnotes={"F1": "Restores sale: previously reported as 120 shares gift, should have been a 300-share sale."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_swap = _screen(swap_orig + [swap_b_to_gift, swap_a_to_gift, swap_restore_b], since_filing_date="2026-10-01")
    assert res_swap["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_swap["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_swap["veto_buy"] is True

    # 41. Pass A0 order-independence: specific price claim ($20) resolves before broader 200-share claim in both orders.
    a0_orig = [
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=200.0, price=20.0),
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=200.0, price=30.0),
    ] + [dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=110.0 + i) for i in range(4, 10)]
    a0_broad = dict(
        a0_orig[1],
        form="4/A",
        accession="0000555555-26-000270",
        filing_date="2026-10-10",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        shares=150.0,
        price=0.0,
        footnotes={"F1": "Corrects previously misreported 200-share sale to a 150-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    a0_specific = dict(
        a0_orig[0],
        form="4/A",
        accession="0000555555-26-000270",
        filing_date="2026-10-10",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        shares=140.0,
        price=0.0,
        footnotes={"F1": "Corrects previously reported sale at $20 to a 140-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    for ordered in ([a0_broad, a0_specific], [a0_specific, a0_broad]):
        res_a0 = _screen(a0_orig + ordered, since_filing_date="2026-10-01")
        assert res_a0["checks"]["insider_trades"]["sales_90d"] == 6
        assert res_a0["checks"]["insider_trades"]["count_uncertain"] is False
        assert res_a0["veto_buy"] is True

    # 42. Linked restoration matches gift revision reported on the linked filing date (Oct 9 vs Oct 8).
    prov_g_orig = [
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=200.0, price=20.0),
        dict(chain_orig_2[0], transaction_date="2026-10-03", shares=300.0, price=30.0),
    ] + [dict(chain_orig_2[0], transaction_date=f"2026-10-0{i}", shares=110.0 + i) for i in range(4, 9)]
    prov_g_a_oct8 = dict(
        prov_g_orig[0],
        form="4/A",
        accession="0000555555-26-000280",
        filing_date="2026-10-08",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        shares=120.0,
        price=0.0,
        footnotes={"F1": "Corrects previously misreported 200-share sale to a 120-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    prov_g_a_oct9 = dict(
        prov_g_orig[0],
        form="4/A",
        accession="0000555555-26-000281",
        filing_date="2026-10-09",
        original_filing_date="2026-10-08",
        transaction_code="G",
        direction="other",
        shares=150.0,
        price=0.0,
        footnotes={"F1": "Corrects previously reported as 120 shares gift to a 150-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    prov_g_b_oct9 = dict(
        prov_g_orig[1],
        form="4/A",
        accession="0000555555-26-000281",
        filing_date="2026-10-09",
        original_filing_date="2026-10-03",
        transaction_code="G",
        direction="other",
        shares=120.0,
        price=0.0,
        footnotes={"F1": "Corrects previously misreported 300-share sale to a 120-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    prov_restore_oct9 = dict(
        prov_g_orig[1],
        form="4/A",
        accession="0000555555-26-000282",
        filing_date="2026-10-10",
        original_filing_date="2026-10-09",
        shares=300.0,
        price=30.0,
        footnotes={"F1": "Restores sale: previously reported as 120 shares gift, should have been a 300-share sale."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_prov_g9 = _screen(
        prov_g_orig + [prov_g_a_oct8, prov_g_a_oct9, prov_g_b_oct9, prov_restore_oct9], since_filing_date="2026-10-01"
    )
    assert res_prov_g9["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_prov_g9["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_prov_g9["veto_buy"] is True

    prov_restore_oct8 = dict(
        prov_g_orig[0],
        form="4/A",
        accession="0000555555-26-000283",
        filing_date="2026-10-10",
        original_filing_date="2026-10-08",
        shares=200.0,
        price=20.0,
        footnotes={"F1": "Restores sale: previously reported as 120 shares gift, should have been a 200-share sale."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    res_prov_g8 = _screen(
        prov_g_orig + [prov_g_a_oct8, prov_g_a_oct9, prov_g_b_oct9, prov_restore_oct8], since_filing_date="2026-10-01"
    )
    assert res_prov_g8["checks"]["insider_trades"]["sales_90d"] == 6
    assert res_prov_g8["checks"]["insider_trades"]["count_uncertain"] is False
    assert res_prov_g8["veto_buy"] is True


def _screen_form4(monkeypatch, rows):
    monkeypatch.setenv("SEC_USER_AGENT", "test-app test@example.com")
    return risk_screening.screen_risk(
        "AAPL",
        inputs={
            "quote": {"pe": 25, "pb": 5},
            "technical": {"signal_score": 60, "buy_signal": "HOLD", "trend": {"overall": "neutral"}},
        },
        run=lambda script, args, **kwargs: (
            {"transactions": rows, "since_filing_date": "2026-08-28"} if script == "sec_edgar.py" else []
        ),
    )


def _form4_sale(shares=100, **fields):
    return {
        "form": "4",
        "accession": "0000000123-26-000001",
        "filing_date": "2026-11-21",
        "transaction_date": "2026-11-20",
        "transaction_code": "S",
        "owners": [{"cik": "123"}],
        "security": "Common Stock",
        "shares": shares,
        "price": 20,
        **fields,
    }


def test_form4_accession_explicitly_adds_all_its_sale_rows(monkeypatch):
    original = _form4_sale()
    amendments = [
        _form4_sale(
            shares,
            form="4/A",
            accession="0000000123-26-000002",
            filing_date="2026-11-22",
            original_filing_date="2026-11-21",
            transaction_date=day,
            remarks="This amendment reports two previously omitted sales.",
        )
        for shares, day in [(200, "2026-11-18"), (300, "2026-11-19")]
    ]
    for remarks in (
        "This amendment adds two sales.",
        "This amendment reports two previously omitted sales.",
        "This amendment reports two additional sales. The original Form 4 was filed on November 21, 2026.",
        "This amendment is being filed to report two sales that were inadvertently omitted from the original Form 4.",
        "This amendment reports two previously unreported sales by the reporting person.",
    ):
        rows = [dict(tx, remarks=remarks) for tx in amendments]
        result = _screen_form4(monkeypatch, [original, *rows])
        assert result["checks"]["insider_trades"]["sales_90d"] == 3
        assert result["checks"]["insider_trades"]["count_uncertain"] is False
        assert any(flag["severity"] == "medium" for flag in result["flags"])

    later_original = _form4_sale(
        200, accession="0000000123-26-000003", filing_date="2026-11-23", transaction_date="2026-11-18"
    )
    result = _screen_form4(monkeypatch, [original, later_original, *amendments])
    assert result["checks"]["insider_trades"]["sales_90d"] == 4

    revised = dict(
        original,
        **{key: amendments[0][key] for key in ("form", "accession", "filing_date", "original_filing_date", "remarks")},
        price=25,
        footnotes={"F1": "The sale price for this row was revised from $20 to $25."},
        footnote_refs={"transactionAmounts/transactionPricePerShare/value": ["F1"]},
    )
    for note in (
        "The sale price for this row was revised from $20 to $25.",
        "The previously reported sale price has been updated to $25.",
        "The price on the previously reported sale is now $25.",
        "The previously reported sale omitted brokerage fees from the sale price.",
    ):
        row = dict(revised, footnotes={"F1": note})
        result = _screen_form4(monkeypatch, [original, row, amendments[1]])
        assert result["checks"]["insider_trades"]["sales_90d"] == 1
        assert result["checks"]["insider_trades"]["count_uncertain"] is True


def test_form4_screen_cli_requires_affirmative_matching_addition_count(monkeypatch):
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = Path(risk_screening.__file__)
    monkeypatch.setenv("SEC_USER_AGENT", "test-app test@example.com")
    monkeypatch.setenv("PYTHONPATH", str(script.parent))
    launch = """
import json, runpy, sys
import _subproc
fixture = json.load(sys.stdin)
_subproc.run_tool = lambda name, *_args, **_kwargs: fixture[name]
sys.argv.pop(0)
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    remarks_cases = (
        (
            2,
            "The previous amendment reported two previously omitted sales. This amendment updates the reported prices.",
        ),
        (2, "This amendment does not report two previously omitted sales."),
        (6, "This amendment reports sıx previously omitted sales."),
        (1, "This amendment reports two previously omitted sales."),
        (1, "This amendment does not report one previously omitted sale."),
        (1, "The previous amendment reported one previously omitted sale."),
        (1, "This amendment reports two additional sales. The original Form 4 was filed on November 21, 2026."),
        (2, "This amendment reports two additional sales. It also updates the price of the previously reported sale."),
        (2, "This amendment reports two additional sales. The previously reported sale prices were also adjusted."),
        (2, "This amendment reports two additional sales. *The director's prior sales were also adjusted."),
        (
            2,
            "This amendment reports two additional sales. *President, the previously reported sale price was adjusted.",
        ),
    )
    for n, remarks, expected in [
        *((n, remarks, n) for n, remarks in remarks_cases),
        (2, "This amendment reports two additional sales.", 4),
    ]:
        originals = [_form4_sale(shares) for shares in range(100, 100 + n)]
        amendments = [
            dict(
                row,
                form="4/A",
                accession="0000000123-26-000002",
                filing_date="2026-11-22",
                original_filing_date="2026-11-21",
                price=25,
                remarks=remarks,
            )
            for row in originals
        ]
        fixture = {
            "stock_data.py": {"pe": 25, "pb": 5},
            "technical.py": {"signal_score": 60, "buy_signal": "HOLD"},
            "sec_edgar.py": {"transactions": originals + amendments, "since_filing_date": "2026-08-28"},
            "search_intel.py": [],
        }
        cli = subprocess.run(
            [sys.executable, "-c", launch, str(script), "screen", "AAPL"],
            input=json.dumps(fixture),
            capture_output=True,
            encoding="utf-8",
            timeout=10,
        )
        assert cli.returncode == 0, cli.stderr
        assert not cli.stderr
        assert json.loads(cli.stdout)["checks"]["insider_trades"]["sales_90d"] == expected


def test_form4_single_omitted_sale_requires_a_distinct_row(monkeypatch):
    original = _form4_sale()
    amendment = dict(
        original,
        form="4/A",
        accession="0000000123-26-000002",
        filing_date="2026-11-22",
        original_filing_date="2026-11-21",
        remarks="This amendment reports the previously omitted sale.",
    )
    exact = _screen_form4(monkeypatch, [original, amendment])["checks"]["insider_trades"]
    assert exact["sales_90d"] == 1
    assert exact["count_uncertain"] is True
    distinct = _screen_form4(monkeypatch, [original, dict(amendment, transaction_date="2026-11-19")])
    assert distinct["checks"]["insider_trades"]["sales_90d"] == 2
    assert distinct["checks"]["insider_trades"]["count_uncertain"] is False
    for remarks in (
        "This amendment is being filed to report a sale transaction that was inadvertently omitted from the original Form 4.",
        "This Form 4 Amendment is filed to include an additional sale of common shares that was omitted from the Form 4 filed on November 21, 2026.\n\n*President, North America Insurance",
    ):
        result = _screen_form4(monkeypatch, [original, dict(amendment, transaction_date="2026-11-19", remarks=remarks)])
        assert result["checks"]["insider_trades"]["sales_90d"] == 2
        assert result["checks"]["insider_trades"]["count_uncertain"] is False

    for note in (
        "This sale was previously omitted. The reporting person's title changed from Vice President to President.",
        "This sale was inadvertently omitted from the previously filed Form 4.",
        "This report adds the sale omitted from the original Form 4 that was not previously reported.",
    ):
        annotated = dict(
            amendment,
            transaction_date="2026-11-19",
            remarks=None,
            footnotes={"F1": note},
            footnote_refs={"transactionDate/value": ["F1"]},
        )
        result = _screen_form4(monkeypatch, [original, annotated])
        assert result["checks"]["insider_trades"]["sales_90d"] == 2
        assert result["checks"]["insider_trades"]["count_uncertain"] is False


def test_form4_unlinked_date_correction_does_not_create_a_sale(monkeypatch):
    originals = [_form4_sale(), _form4_sale(200, transaction_date="2026-11-19")]
    context = {
        "form": "4/A",
        "accession": "0000000123-26-000002",
        "filing_date": "2026-11-22",
        "original_filing_date": "2026-11-21",
        "remarks": "Corrects the 200-share sale date from November 19, 2026 to November 20, 2026.",
    }
    result = _screen_form4(monkeypatch, originals + [_form4_sale(**context), _form4_sale(200, **context)])
    assert result["checks"]["insider_trades"]["sales_90d"] == 2
    assert result["checks"]["insider_trades"]["count_uncertain"] is True
    assert not result["flags"]


def test_form4_undated_sale_does_not_change_dated_sales_veto(monkeypatch):
    originals = [_form4_sale(shares) for shares in range(100, 106)]
    undated = _form4_sale(300, accession="0000000123-26-000002", transaction_date=None)
    result = _screen_form4(monkeypatch, [*originals, undated])
    assert result["checks"]["insider_trades"]["sales_90d"] == 6
    assert result["checks"]["insider_trades"]["count_uncertain"] is True
    assert result["veto_buy"] is True
    assert any("date" in warning for warning in result["warnings"])
    assert not any("revision" in warning for warning in result["warnings"])


def test_form4_reclassification_reservation_does_not_create_a_sale(monkeypatch):
    originals = [
        _form4_sale(),
        _form4_sale(200, transaction_date="2026-11-19"),
        _form4_sale(300, transaction_date="2026-11-18"),
    ]
    context = {
        "form": "4/A",
        "accession": "0000000123-26-000002",
        "filing_date": "2026-11-22",
        "original_filing_date": "2026-11-21",
    }
    gift = _form4_sale(
        transaction_code="G",
        footnotes={"F1": "Corrects the sale of 100 shares to a gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
        **context,
    )
    result = _screen_form4(monkeypatch, originals + [gift, _form4_sale(**context)])
    assert result["checks"]["insider_trades"]["sales_90d"] == 2
    assert not result["flags"]


def test_form4_conflicting_gift_revisions_do_not_create_a_restorable_state(monkeypatch):
    def file_revision(row, day, **fields):
        return dict(
            row,
            form="4/A",
            accession=f"0000000123-26-{day - 20:06d}",
            filing_date=f"2026-11-{day}",
            original_filing_date=f"2026-11-{day - 1}",
            **fields,
        )

    original = _form4_sale()
    gift = file_revision(
        original,
        22,
        shares=120,
        transaction_code="G",
        footnotes={"F1": "Corrects previously reported 100-share sale to a 120-share gift."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    restore = file_revision(
        original,
        24,
        footnotes={"F1": "Corrects the gift previously reported as 150 shares. It should have been a 100-share sale."},
        footnote_refs={"transactionCoding/transactionCode": ["F1"]},
    )
    stated_prior = "Corrects this gift from 120 shares to {} shares."
    no_prior = "Corrects the number of gift shares to {}."
    for templates in ((stated_prior, stated_prior), (no_prior, no_prior), (stated_prior, no_prior)):
        claims = [
            file_revision(
                gift,
                23,
                shares=shares,
                footnotes={"F1": template.format(shares)},
            )
            for shares, template in zip((150, 160), templates)
        ]
        for ordered in (claims, claims[::-1]):
            result = _screen_form4(monkeypatch, [original, gift, *ordered, restore])
            assert result["checks"]["insider_trades"]["sales_90d"] == 0
            assert result["checks"]["insider_trades"]["count_uncertain"] is True

    prior_sale_price = dict(
        gift,
        transaction_code="S",
        shares=100,
        price=24,
        footnotes={"F1": "Corrects the sale price from $20 to $24."},
    )
    # The historical/current signatures use exact matching, the new prices use share matching.
    for last_filed, history, prices in (
        (22, [gift], (25, 30)),
        (23, [prior_sale_price, file_revision(gift, 23, price=24)], (20, 24)),
    ):
        price_claims = [
            file_revision(
                gift,
                last_filed + 1,
                shares=100,
                price=price,
                footnotes={"F1": f"Corrects the gift price to ${price}."},
            )
            for price in prices
        ]
        restore_price = file_revision(
            restore,
            last_filed + 2,
            price=25 if prices[0] == 20 else 20,
            footnotes={
                "F1": f"Corrects the gift previously reported at ${prices[0]}. It should have been a 100-share sale."
            },
        )
        for ordered in (price_claims, price_claims[::-1]):
            check = _screen_form4(monkeypatch, [original, *history, *ordered, restore_price])["checks"][
                "insider_trades"
            ]
            assert check["sales_90d"] == 0
            assert check["count_uncertain"] is True
        check = _screen_form4(monkeypatch, [original, *history, price_claims[0], restore_price])["checks"][
            "insider_trades"
        ]
        assert check["sales_90d"] == 1
        assert check["count_uncertain"] is False

    # A first-time reclassification and a revision to an existing gift can coexist.
    other_sale = dict(original, price=24)
    gift = dict(
        gift,
        price=None,
        footnotes={"F1": "Corrects the previously reported sale of 100 shares at $20 to a 120-share gift."},
    )
    revise_gift = file_revision(
        gift, 23, shares=150, footnotes={"F1": "Corrects this gift from 120 shares to 150 shares."}
    )
    new_gift = file_revision(gift, 23, shares=100, footnotes={"F1": "Corrects the 100-share sale to a gift."})
    revise_gift["original_filing_date"] = new_gift["original_filing_date"] = original["filing_date"]
    share_correction = dict(
        new_gift, shares=130, footnotes={"F1": "Corrects the previously reported 100-share sale to a 130-share gift."}
    )
    prefixed = dict(
        share_correction,
        footnotes={"F1": "This amendment corrects the previously reported 100-share sale to a 130-share gift."},
    )
    passive = dict(
        share_correction,
        footnotes={
            "F1": "The 100-share sale was previously reported in error and is reclassified to a 130-share gift."
        },
    )
    code_corrections = [
        dict(new_gift, footnotes={"F1": f"{verb} the transaction code from S to G."})
        for verb in ("Corrects", "Changes", "Reclassifies")
    ]
    for sale_to_gift in (new_gift, share_correction, prefixed, passive, *code_corrections):
        for ordered in ([revise_gift, sale_to_gift], [sale_to_gift, revise_gift]):
            check = _screen_form4(monkeypatch, [original, other_sale, gift, *ordered])["checks"]["insider_trades"]
            assert check["sales_90d"] == 0
            assert check["count_uncertain"] is False

    for code, note in (
        ("D", "Corrects the transaction code of the gift from G to D."),
        ("G", "Restates the gift after the previous amendment reclassified a sale to a gift."),
        ("G", "Corrects the sale previously reclassified to a gift to update the reported share amount."),
    ):
        restated_gift = dict(new_gift, transaction_code=code, footnotes={"F1": note})
        check = _screen_form4(monkeypatch, [original, other_sale, gift, restated_gift])["checks"]["insider_trades"]
        assert check["sales_90d"] == 1
        assert check["count_uncertain"] is False

    same_size_gift = dict(gift, shares=100)
    repeated_gift = dict(new_gift, footnotes={"F1": "This amendment corrects the 100-share sale to a gift."})
    check = _screen_form4(monkeypatch, [original, other_sale, same_size_gift, repeated_gift])["checks"][
        "insider_trades"
    ]
    assert check["sales_90d"] == 1


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
                "since_filing_date": "2026-08-01",
                "transactions": [{"code": "S", "transaction_date": f"2026-10-0{i}"} for i in (1, 2, 3)],
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
