"""Rule62 plans preserve the selected strategy without an account or order executor."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tools.trend_strategy import analyze


def example():
    dates = pd.bdate_range(end="2026-02-02", periods=221)
    base = np.arange(1, 222, dtype=float)
    histories = {
        name: [
            {"date": str(day.date()), "close": float(price), "volume": 100, "adjusted": "qfq"}
            for day, price in zip(dates, values, strict=True)
        ]
        for name, values in (("AAA", base), ("BBB", base**2))
    }
    return {"market": "US", "as_of": "2026-02-02", "histories": histories, "slots": 2}


def test_risk_discount_uses_all_pool_sigma_and_never_expands_low_risk():
    result = analyze(example())
    assert result["model"] == "sma200_risk_budget"
    assert result["validation_status"] == "not_validated"
    assert result["status"] == "monthly_observation"
    assert result["selected"] == ["BBB", "AAA"]
    stocks = result["stocks"]
    assert stocks["AAA"]["sma200"] == 121.5
    assert stocks["AAA"]["sma200_21_bars_ago"] == 100.5
    assert stocks["AAA"]["new_budget_fraction"] == pytest.approx(0.4352)
    assert stocks["BBB"]["new_budget_fraction"] == pytest.approx(0.3264)
    assert result["execution"]["requires_available_cash_and_capacity"] is True


def test_real_held_stock_has_priority_and_pending_exit_survives_recovery():
    payload = example()
    payload.update(slots=1, held_symbols=["AAA"])
    result = analyze(payload)
    assert result["selected"] == ["AAA"]
    assert result["stocks"]["AAA"]["action"] == "hold"
    assert result["stocks"]["AAA"]["new_budget_fraction"] == 0
    payload["pending_exit_symbols"] = ["AAA"]
    result = analyze(payload)
    assert result["selected"] == ["BBB"]
    assert result["stocks"]["AAA"]["action"] == "exit_pending"
    assert result["stocks"]["AAA"]["reason"] == "previously_locked_exit"


def test_monthly_clock_does_not_turn_current_holdings_into_historical_orders():
    payload = example()
    for records in payload["histories"].values():
        records.append({**records[-1], "date": "2026-02-03", "close": records[-1]["close"] * 2})
    previous = analyze(payload)
    payload["as_of"] = "2026-02-03"
    current = analyze(payload)
    assert current["status"] == "waiting_for_monthly_observation"
    assert current["selected"] == []
    assert all(row["new_budget_fraction"] == 0 for row in current["stocks"].values())
    payload["as_of"] = "2026-02-02"
    assert analyze(payload) == previous


def test_cli_bad_input_still_returns_clean_json(tmp_path):
    path = tmp_path / "输入.json"
    path.write_text('{"market":"US"}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "tools/trend_strategy.py"), "--input", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "error" in json.loads(result.stdout)
    assert "Traceback" not in result.stdout + result.stderr


def test_insufficient_warmup_and_zero_variance_allow_an_empty_portfolio():
    payload = example()
    payload["histories"] = {"AAA": payload["histories"]["AAA"][-200:]}
    payload["slots"] = 1
    result = analyze(payload)
    assert result["selected"] == []
    assert not result["stocks"]["AAA"]["new_entry_eligible"]
    for row in payload["histories"]["AAA"]:
        row["close"] = 10.0
    assert analyze(payload)["reference_volatility"] is None


def test_unobservable_real_holding_keeps_its_slot_and_never_gets_an_addition():
    payload = example()
    payload.update(slots=1, held_symbols=["AAA"])
    payload["histories"]["AAA"][-1]["volume"] = 0
    result = analyze(payload)
    assert result["selected"] == ["AAA"]
    assert result["stocks"]["AAA"]["reason"] == "unobservable_held_reserves_slot"
    assert result["reference_volatility"] == result["stocks"]["BBB"]["sigma63"]


def test_known_held_price_failure_exits_even_while_long_average_is_rising():
    payload = example()
    payload["held_symbols"] = ["AAA"]
    payload["histories"]["AAA"][-1]["close"] = 1
    result = analyze(payload)
    assert result["stocks"]["AAA"]["action"] == "exit_next_valid_open"
    assert result["stocks"]["AAA"]["new_budget_fraction"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"slots": True},
        {"slots": 0},
        {"allocation": 1},
        {"market": "JP"},
        {"held_symbols": ["MISSING"]},
        {"held_symbols": ["AAA", "AAA"]},
        {"pending_exit_symbols": ["AAA"]},
        {"corporate_actions": []},
    ],
)
def test_invalid_state_and_silent_strategy_changes_are_rejected(change):
    with pytest.raises(ValueError):
        analyze({**example(), **change})


def test_bad_prices_duplicate_dates_and_mixed_markets_are_rejected():
    for value in [True, -1, float("nan")]:
        payload = example()
        payload["histories"]["AAA"][-1]["close"] = value
        with pytest.raises(ValueError):
            analyze(payload)
    payload = example()
    payload["histories"]["AAA"][-1]["date"] = payload["histories"]["AAA"][-2]["date"]
    with pytest.raises(ValueError):
        analyze(payload)
    payload = example()
    payload["histories"]["600036"] = payload["histories"].pop("AAA")
    with pytest.raises(ValueError):
        analyze(payload)


def test_hk_provider_padding_is_accepted_but_aliases_cannot_double_count():
    payload = example()
    payload.update(market="HK", slots=1)
    payload["histories"] = {"00700.HK": payload["histories"]["AAA"]}
    assert analyze(payload)["selected"] == ["00700.HK"]
    payload["histories"]["0700.HK"] = payload["histories"]["00700.HK"]
    with pytest.raises(ValueError):
        analyze(payload)


def test_true_held_stock_is_not_exited_when_new_entry_slope_turns_negative():
    payload = example()
    payload.update(slots=1, held_symbols=["AAA"])
    values = list(range(321, 100, -1))
    values[-1] = 250
    for row, price in zip(payload["histories"]["AAA"], values, strict=True):
        row["close"] = price
    result = analyze(payload)
    assert result["stocks"]["AAA"]["distance"] > 0
    assert not result["stocks"]["AAA"]["rising_sma200"]
    assert result["stocks"]["AAA"]["action"] == "hold"


def test_ineligible_stock_still_contributes_to_reference_volatility():
    payload = example()
    payload["histories"]["CCC"] = [{**row, "close": row["close"] ** -3} for row in payload["histories"]["AAA"]]
    result = analyze(payload)
    assert not result["stocks"]["CCC"]["new_entry_eligible"]
    assert result["reference_volatility"] == result["stocks"]["BBB"]["sigma63"]
    assert result["stocks"]["BBB"]["new_budget_fraction"] == pytest.approx(0.4352)


@pytest.mark.parametrize("kind", ["duplicate_members", "deep_json"])
def test_cli_rejects_duplicate_members_and_deep_json_without_tracebacks(tmp_path, kind):
    path = tmp_path / "bad.json"
    if kind == "duplicate_members":
        payload = example()
        rows = json.dumps(payload["histories"]["AAA"])
        body = '{"market":"US","as_of":"2026-02-02","histories":{"AAA":' + rows + ',"AAA":' + rows + "}}"
    else:
        body = "[" * 20000 + "0" + "]" * 20000
    path.write_text(body)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "tools/trend_strategy.py"), "--input", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "error" in json.loads(result.stdout)
    assert "Traceback" not in result.stdout + result.stderr


def test_equivalent_hk_padding_does_not_change_tie_break_selection():
    payload = example()
    rows = payload["histories"]["AAA"]
    payload.update(market="HK", slots=1, histories={"00700.HK": rows, "0388.HK": rows})
    assert analyze(payload)["selected"] == ["0388.HK"]
    payload["histories"]["0700.HK"] = payload["histories"].pop("00700.HK")
    assert analyze(payload)["selected"] == ["0388.HK"]


def test_cli_rejects_fifo_without_waiting_for_a_writer(tmp_path):
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO is POSIX-only")
    path = tmp_path / "input-fifo"
    os.mkfifo(path)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "tools/trend_strategy.py"), "--input", str(path)],
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert result.returncode != 0
    assert "error" in json.loads(result.stdout)


def test_cli_rejects_oversized_regular_input_before_parsing(tmp_path):
    path = tmp_path / "too-large.json"
    with path.open("wb") as stream:
        stream.truncate(32 * 1024 * 1024 + 1)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "tools/trend_strategy.py"), "--input", str(path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "error" in json.loads(result.stdout)
    assert "Traceback" not in result.stdout + result.stderr


def test_stock_codes_fetch_prices_without_user_supplied_files(monkeypatch):
    from tools import trend_strategy

    payload = example()
    calls = []

    def fetch(symbol, count):
        calls.append((symbol, "daily", count))
        return payload["histories"][symbol]

    monkeypatch.setattr(trend_strategy, "_fetch_daily_history", fetch, raising=False)
    monkeypatch.setattr(trend_strategy, "latest_closed_date", lambda market: "2026-02-02", raising=False)
    result = trend_strategy.fetch_plan({"symbols": ["aaa", "bbb"], "slots": 2})
    assert result["selected"] == ["BBB", "AAA"]
    assert result["stocks"]["BBB"]["new_budget_fraction"] == pytest.approx(0.3264)
    assert result["as_of"] == "2026-02-02"
    assert {call[:2] for call in calls} == {("AAA", "daily"), ("BBB", "daily")}
    assert all(call[2] >= 360 for call in calls)


def test_fetch_failure_does_not_silently_delete_a_pool_member(monkeypatch):
    from tools import trend_strategy

    sample = example()
    monkeypatch.setattr(trend_strategy, "latest_closed_date", lambda market: "2026-02-02")
    monkeypatch.setattr(
        trend_strategy,
        "_fetch_daily_history",
        lambda n, *args: sample["histories"][n] if n == "AAA" else {"error": "private provider response"},
    )
    result = trend_strategy.fetch_plan({"symbols": ["AAA", "BBB"]})
    assert result["error_code"] == "incomplete_pool" and result["failed_symbols"] == ["BBB"]
    assert "selected" not in result and "private" not in json.dumps(result)


def test_native_hk_symbols_and_held_state_share_the_same_identity(monkeypatch):
    from tools import trend_strategy

    sample = example()["histories"]["AAA"]
    monkeypatch.setattr(trend_strategy, "latest_closed_date", lambda market: "2026-02-02")
    monkeypatch.setattr(trend_strategy, "_fetch_daily_history", lambda *args: sample)
    result = trend_strategy.fetch_plan({"symbols": ["00700.hk"], "held_symbols": ["0700.HK"]})
    assert result["stocks"]["0700.HK"]["action"] == "hold"


@pytest.mark.parametrize(
    "native_request",
    [
        {"symbols": ["600036", "AAPL"]},
        {"symbols": ["AAA", "aaa"]},
        {"symbols": ["0700.HK", "00700.HK"]},
        {"symbols": ["AAA"], "slots": True},
        {"symbols": ["AAA"], "held_symbols": ["BBB"]},
        {"symbols": ["AAA"], "as_of": "2026-02-03"},
    ],
)
def test_invalid_native_request_is_rejected_before_fetching(monkeypatch, native_request):
    from tools import trend_strategy

    monkeypatch.setattr(trend_strategy, "latest_closed_date", lambda market: "2026-02-02")
    monkeypatch.setattr(
        trend_strategy, "_fetch_daily_history", lambda *args: pytest.fail("Invalid request must not fetch")
    )
    with pytest.raises(ValueError):
        trend_strategy.fetch_plan(native_request)


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        ("2025-11-28T17:59:00Z", "2025-11-26"),
        ("2025-11-28T18:01:00Z", "2025-11-28"),
        ("2026-03-09T19:59:00Z", "2026-03-06"),
        ("2026-03-09T20:01:00Z", "2026-03-09"),
    ],
)
def test_default_cutoff_respects_exchange_early_close_and_dst(at, expected):
    from tools.trend_strategy import latest_closed_date

    assert latest_closed_date("US", now=at) == expected


def test_fetch_leaf_timeout_fits_the_shared_host_budget(monkeypatch):
    from tools import trend_strategy
    from tools._subproc import command_timeout

    calls = []

    def cli(script, args, *, timeout):
        calls.append((script, args, timeout))
        return example()["histories"]["AAA"]

    monkeypatch.setattr(trend_strategy, "run_tool", cli)
    monkeypatch.setattr(trend_strategy, "latest_closed_date", lambda market: "2026-02-02")
    result = trend_strategy.fetch_plan({"symbols": ["AAA"]})
    assert result["selected"] == ["AAA"]
    assert calls == [("stock_data.py", ["kline", "AAA", "--period", "daily", "--count", "360"], 30)]
    assert command_timeout("trend_strategy.py", ["fetch", "--input", "internal.json"]) > 17 * calls[0][2]


def test_integral_slot_numbers_have_the_same_meaning_across_hosts():
    from tools.trend_strategy import analyze

    payload = example()
    payload["slots"] = 1.0
    assert analyze(payload)["slots"] == 1
    for invalid in (True, 1.5, float("nan"), float("inf")):
        payload["slots"] = invalid
        with pytest.raises(ValueError):
            analyze(payload)


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        ("2026-10-16T08:01:00Z", "2026-10-15"),
        ("2026-10-16T08:11:00Z", "2026-10-16"),
        ("2025-12-24T04:01:00Z", "2025-12-23"),
        ("2025-12-24T04:11:00Z", "2025-12-24"),
    ],
)
def test_hk_cutoff_waits_for_closing_auction_on_full_and_half_days(at, expected):
    from tools.trend_strategy import latest_closed_date

    assert latest_closed_date("HK", now=at) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["fetch"],
        ["unknown", "--input", "unused.json"],
        ["fetch", "--input", "unused.json", "--unexpected", "private-value"],
    ],
)
def test_cli_argument_errors_return_safe_json(argv):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "tools/trend_strategy.py"), *argv],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "invalid_arguments"
    assert payload["error"]
    assert result.stderr == ""
    assert "private-value" not in result.stdout
