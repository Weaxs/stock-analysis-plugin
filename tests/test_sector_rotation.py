"""Public CLI checks for disclosed sector-rotation paper calculations."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/sector-rotation/scripts/rotation_metrics.py"


def test_shared_analysis_combines_metrics_without_generating_model_signals():
    from tools.sector_rotation import analyze

    result = analyze(
        {
            "prediction_samples": [{"symbol": "XLK", "samples": [0.0] * 8 + [-10.0, 10.0]}],
            "trade_counts": [{"symbol": "XLK", "buys": 4, "wins": 3, "streak": 2}],
        }
    )
    assert result["confidence"]["XLK"]["mass_within_one_std_of_median"] == 0.8
    assert result["allocation"]["assets"][0]["raw_weight"] == 2.25
    assert result["model_status"] == "not_implemented_missing_specification"
    assert result["is_buy_signal"] is False


@pytest.fixture
def rotation_request(make_kline_data):
    import pandas as pd

    datasets = []
    for symbol, last in (("AAA", 110.0), ("BBB", 90.0)):
        bars = make_kline_data(n=10, trend="flat")
        for i, (bar, day) in enumerate(zip(bars, pd.bdate_range("2026-01-05", periods=10), strict=True)):
            close = last if i in (4, 9) else 100.0
            bar.update(date=str(day.date()), open=100.0, high=max(100.0, close), low=min(100.0, close), close=close)
        datasets.append({"symbol": symbol, "currency": "USD", "price_basis": "qfq", "bars": bars})
    return {
        "datasets": datasets,
        "start_date": "2026-01-12",
        "end_date": "2026-01-16",
        "warmup_weeks": 1,
        "capital": 700.0,
        "cost_per_side": 0,
    }


def test_allocation_backtest_uses_only_prior_closed_trades(rotation_request):
    from tools.sector_rotation import backtest

    result = backtest(rotation_request)
    group = result["groups"][0]
    assert group["currency"] == "USD"
    assert group["evaluated_weeks"] == 1
    assert group["equal_weight"]["final_equity"] == pytest.approx(700)
    assert group["historical_weight"]["final_equity"] == pytest.approx(730)
    assert group["allocations"][0]["weights"] == pytest.approx({"AAA": 5 / 7, "BBB": 2 / 7})
    assert result["paper_reproduction"] == "allocation_ablation_only"


def test_incomplete_first_week_cannot_supply_warmup(rotation_request):
    from tools.sector_rotation import backtest

    for dataset in rotation_request["datasets"]:
        dataset["bars"] = dataset["bars"][2:]
    with pytest.raises(ValueError):
        backtest(rotation_request)


@pytest.mark.parametrize("gap", ["missing_bar", "zero_volume", "missing_tail"])
def test_incomplete_evaluation_cannot_select_profitable_weeks(rotation_request, tmp_path, gap):
    from tools.sector_rotation import DataQualityError, backtest

    rotation_request["end_date"] = "2026-01-23"
    for dataset in rotation_request["datasets"]:
        dataset["bars"].extend({**dataset["bars"][-1], "date": f"2026-01-{day}"} for day in range(20, 24))
    bars = rotation_request["datasets"][0]["bars"]
    if gap == "zero_volume":
        next(bar for bar in bars if bar["date"] == "2026-01-14")["volume"] = 0
    else:
        dataset = rotation_request["datasets"][0]
        dataset["bars"] = [
            bar
            for bar in bars
            if (gap != "missing_bar" or bar["date"] != "2026-01-14")
            and (gap != "missing_tail" or bar["date"] < "2026-01-20")
        ]
    with pytest.raises(DataQualityError):
        backtest(rotation_request)
    result, data = run_check(tmp_path, "backtest", rotation_request)
    assert result.returncode != 0
    assert data["error_code"] == "market_data_requires_review"
    assert "groups" not in data


def test_backtest_budget_includes_both_sides_of_cost(rotation_request):
    from tools.sector_rotation import backtest

    rotation_request["cost_per_side"] = 0.01
    group = backtest(rotation_request)["groups"][0]
    assert group["equal_weight"]["final_equity"] == pytest.approx(686.1386138613861)
    assert group["equal_weight"]["costs_paid"] == pytest.approx(13.861386138613861)
    assert group["historical_weight"]["final_equity"] == pytest.approx(715.5445544554456)


def test_currency_and_market_groups_do_not_share_capital(rotation_request):
    from tools.sector_rotation import backtest

    rotation_request["datasets"][0].update(symbol="2800.HK", currency="HKD")
    rotation_request["datasets"][1].update(symbol="2822.HK", currency="USD")
    result = backtest(rotation_request)
    assert result["capital_scope"] == "independent_per_market_and_quote_currency"
    assert {g["currency"] for g in result["groups"]} == {"HKD", "USD"}
    assert [g["equal_weight"]["final_equity"] for g in result["groups"]] == pytest.approx([770, 630])


@pytest.mark.parametrize(
    "symbol,currency", [("600036", "CNY"), ("1306.T", "JPY"), ("069500.KS", "KRW"), ("0050.TW", "TWD")]
)
def test_all_six_markets_use_exchange_sessions(rotation_request, symbol, currency):
    from tools.sector_rotation import backtest

    rotation_request["datasets"] = rotation_request["datasets"][:1]
    rotation_request["datasets"][0].update(symbol=symbol, currency=currency)
    if symbol == "1306.T":
        rotation_request["datasets"][0]["bars"] = [
            b for b in rotation_request["datasets"][0]["bars"] if b["date"] != "2026-01-12"
        ]
    group = backtest(rotation_request)["groups"][0]
    assert group["symbols"] == [symbol]
    # Jan 12 is a JP holiday; the actual first session remains an exchange-calendar decision.
    assert group["period"]["end"] == "2026-01-16"
    if symbol == "1306.T":
        assert group["period"]["start"] == "2026-01-13"


def test_future_extension_does_not_change_prior_decision(rotation_request):
    from copy import deepcopy

    from tools.sector_rotation import backtest

    original = backtest(rotation_request)
    extended = deepcopy(rotation_request)
    for dataset in extended["datasets"]:
        dataset["bars"].append({**dataset["bars"][-1], "date": "2026-01-20", "close": 1, "low": 1})
    assert backtest(extended) == original


def test_adjustment_roundoff_does_not_reject_valid_ohlc(rotation_request):
    import math

    from tools.sector_rotation import backtest

    bar = rotation_request["datasets"][0]["bars"][-1]
    bar["close"] = math.nextafter(bar["high"], math.inf)
    assert backtest(rotation_request)["groups"][0]["historical_weight"]["final_equity"] == pytest.approx(730)


@pytest.mark.parametrize(
    "kind", ["split_jump", "unknown_basis", "duplicate_date", "bad_ohlc", "unverified_currency", "zero_volume"]
)
def test_bad_price_pool_never_produces_a_partial_result(rotation_request, kind):
    from tools.sector_rotation import backtest

    dataset = rotation_request["datasets"][0]
    if kind == "split_jump":
        dataset["bars"][5].update(open=50, close=50, high=50, low=50)
    elif kind == "unknown_basis":
        dataset["price_basis"] = "unknown"
    elif kind == "duplicate_date":
        dataset["bars"][1]["date"] = dataset["bars"][0]["date"]
    elif kind == "bad_ohlc":
        dataset["bars"][5]["high"] = 90
    elif kind == "unverified_currency":
        dataset["currency"] = None
    else:
        dataset["bars"][-1]["volume"] = 0
    with pytest.raises(ValueError):
        backtest(rotation_request)


def test_missing_member_acquisition_fails_the_whole_request(monkeypatch):
    import tools.sector_rotation as rotation

    monkeypatch.setattr(
        rotation, "fetch_dataset", lambda *args: (_ for _ in ()).throw(ValueError("private provider body"))
    )
    result = rotation.backtest({"symbols": ["XLK", "XLE"], "start_date": "2026-01-12", "end_date": "2026-01-16"})
    assert result["error_code"] == "incomplete_pool"
    assert result["failed_symbols"] == ["XLK", "XLE"]
    assert "private provider body" not in json.dumps(result)


def test_code_mode_respects_explicit_history_start(monkeypatch, rotation_request):
    from copy import deepcopy

    import tools.sector_rotation as rotation

    histories = {d["symbol"]: d for d in rotation_request["datasets"]}
    monkeypatch.setattr(rotation, "fetch_dataset", lambda symbol, count: deepcopy(histories[symbol]))
    request = {k: v for k, v in rotation_request.items() if k != "datasets"}
    request.update(symbols=["AAA", "BBB"], history_start_date="2026-01-05")
    assert rotation.backtest(request)["groups"][0]["historical_weight"]["final_equity"] == pytest.approx(730)
    request["history_start_date"] = "2026-01-07"
    with pytest.raises(ValueError):
        rotation.backtest(request)


def test_new_tool_cli_and_hermes_execute_real_backtest(tmp_path, rotation_request):
    from hermes.tools import backtest_rotation_allocation, calculate_rotation_metrics

    path = tmp_path / "rotation.json"
    path.write_text(json.dumps(rotation_request))
    cli = subprocess.run(
        [sys.executable, str(SCRIPT.parents[3] / "tools/sector_rotation.py"), "backtest", "--input", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert cli.returncode == 0
    assert json.loads(cli.stdout)["groups"][0]["historical_weight"]["final_equity"] == pytest.approx(730)
    result = json.loads(backtest_rotation_allocation(rotation_request))
    assert result["groups"][0]["historical_weight"]["final_equity"] == pytest.approx(730)
    result = json.loads(
        calculate_rotation_metrics({"trade_counts": [{"symbol": "X", "buys": 4, "wins": 3, "streak": 2}]})
    )
    assert result["allocation"]["assets"][0]["raw_weight"] == 2.25


def run_check(tmp_path, command, data):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), command, str(path)], capture_output=True, text=True, timeout=10
    )
    return result, json.loads(result.stdout)


def test_confidence_reports_distribution_concentration_not_profit_probability(tmp_path):
    # Eight central values and two outliers: exactly 80% within one population SD of the median.
    result, data = run_check(tmp_path, "confidence", [0.0] * 8 + [-10.0, 10.0])
    assert result.returncode == 0
    assert data["median"] == 0
    assert data["population_std"] == pytest.approx(4.47213595499958)
    assert data["mass_within_one_std_of_median"] == 0.8
    assert data["concentration_criterion_met"] is True
    assert data["is_buy_signal"] is False
    assert data["paper_reproduction"] == "partial_calculations_only"


def test_large_finite_predictions_keep_finite_median_and_correct_mass(tmp_path):
    result, data = run_check(tmp_path, "confidence", [1e308, 1e308])
    assert result.returncode == 0
    assert data["median"] == 1e308
    assert data["population_std"] == 0
    assert data["mass_within_one_std_of_median"] == 1


def test_allocation_uses_paper_counts_and_leaves_zero_history_undefined(tmp_path):
    result, data = run_check(
        tmp_path,
        "allocation",
        [
            {"symbol": "510300", "buys": 4, "wins": 3, "streak": 2},
            {"symbol": "1306.T", "buys": 2, "wins": 0, "streak": 0},
            {"symbol": "0050.TW", "buys": 0, "wins": 0, "streak": 0},
        ],
    )
    assert result.returncode == 0
    assert [row["raw_weight"] for row in data["assets"]] == [2.25, 1.0, None]
    assert data["assets"][2]["unavailable_reason"] == "no_paper_initialization_for_zero_buys"
    assert data["is_allocation_plan"] is False


def test_negative_concentrated_predictions_and_zero_variance_are_not_buys(tmp_path):
    result, data = run_check(tmp_path, "confidence", [-1.0] * 8 + [-11.0, 9.0])
    assert result.returncode == 0
    assert data["median"] == -1
    assert data["concentration_criterion_met"] is True
    assert data["is_buy_signal"] is False
    _, data = run_check(tmp_path, "confidence", [0.5] * 10)
    assert data["mass_within_one_std_of_median"] == 1
    assert data["concentration_criterion_met"] is None
    _, data = run_check(tmp_path, "confidence", list(range(10)))
    assert data["concentration_criterion_met"] is False


@pytest.mark.parametrize(
    ("command", "data"),
    [
        ("confidence", []),
        ("confidence", [0, True]),
        ("confidence", [0, float("nan")]),
        ("confidence", [0, float("inf")]),
        ("confidence", ["0", 1]),
        ("allocation", [{"symbol": "X", "buys": 1, "wins": 2, "streak": 0}]),
        ("allocation", [{"symbol": "X", "buys": True, "wins": 0, "streak": 0}]),
        ("allocation", [{"symbol": "X", "buys": 1, "wins": 0, "streak": 1}]),
        ("allocation", [{"symbol": " X", "buys": 0, "wins": 0, "streak": 0}]),
        ("allocation", [{"symbol": "X", "buys": 0, "wins": 0, "streak": 0, "budget": 1}]),
        ("allocation", [{"symbol": "X", "buys": 1, "wins": 0, "streak": 0}] * 2),
    ],
)
def test_invalid_inputs_fail_with_clean_json(tmp_path, command, data):
    result, data = run_check(tmp_path, command, data)
    assert result.returncode != 0
    assert "error" in data
    assert "Traceback" not in result.stdout + result.stderr


def test_json_cli_rejects_duplicates_and_invalid_arguments(tmp_path):
    path = tmp_path / "input.json"
    path.write_text('[{"symbol":"X","buys":1,"buys":2,"wins":0,"streak":0}]')
    for argv in (["allocation", str(path)], [], ["confidence", str(path), "--unknown"]):
        result = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
        assert "error" in json.loads(result.stdout)
        assert "Traceback" not in result.stdout + result.stderr
