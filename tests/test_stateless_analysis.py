import io
import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "stateless_analysis.py"


def invoke(command, payload):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert not result.stderr, result.stderr
    return result.returncode, json.loads(result.stdout)


def test_position_size_respects_cash_risk_lots_and_costs():
    code, result = invoke(
        "position_size",
        {
            "equity": 100000,
            "cash": 20000,
            "entry_price": 100,
            "stop_price": 95,
            "risk_fraction": 0.01,
            "max_position_fraction": 0.3,
            "lot_size": 100,
            "fee_rate": 0.001,
            "slippage_rate": 0.001,
        },
    )
    assert code == 0
    assert result["quantity"] == 100
    assert result["cash_required"] == pytest.approx(10020.01)
    assert result["planned_loss"] == pytest.approx(539)
    assert result["risk_budget"] == 1000


def test_portfolio_converts_currency_and_preserves_missing_stop_risk():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "CNY",
            "cash": 300,
            "fx_rates": [{"currency": "USD", "rate": 7}],
            "positions": [
                {
                    "symbol": "AAPL",
                    "quantity": 1,
                    "price": 100,
                    "cost": 90,
                    "currency": "USD",
                    "sector": "tech",
                    "as_of": "2026-01-02",
                },
                {
                    "symbol": "600519",
                    "quantity": 10,
                    "price": 100,
                    "cost": 110,
                    "currency": "CNY",
                    "sector": "consumer",
                    "as_of": "2026-01-02",
                },
            ],
        },
    )
    assert code == 0
    assert result["total_equity"] == 2000
    assert result["price_pnl_base"] == -30
    assert result["currency_exposure"]["USD"] == pytest.approx(0.35)
    assert result["stop_loss_risk_base"] is None
    assert result["positions"][1]["weight"] == 0.5


def test_portfolio_missing_fx_is_error_not_partial_total():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "CNY",
            "cash": 0,
            "positions": [{"symbol": "AAPL", "quantity": 1, "price": 100, "currency": "USD"}],
        },
    )
    assert code == 1 and "error" in result


def test_peer_comparison_excludes_incompatible_and_missing_metrics():
    code, result = invoke(
        "peers",
        {
            "companies": [
                {
                    "symbol": "A",
                    "period": "2025",
                    "metrics": [
                        {"name": "pe", "value": 10, "unit": "multiple", "basis": "TTM"},
                        {"name": "roe", "value": 15, "unit": "percent", "basis": "annual"},
                    ],
                },
                {
                    "symbol": "B",
                    "period": "2025",
                    "metrics": [
                        {"name": "pe", "value": 20, "unit": "multiple", "basis": "TTM"},
                        {"name": "roe", "value": 0.2, "unit": "fraction", "basis": "annual"},
                    ],
                },
                {"symbol": "C", "period": "2025", "metrics": []},
            ]
        },
    )
    assert code == 0
    assert result["metrics"]["pe"]["ascending"] == ["A", "B"]
    assert result["metrics"]["pe"]["missing"] == ["C"]
    assert result["metrics"]["roe"]["ascending"] is None


def test_disclosure_changes_link_exact_lines_and_negative_base_is_not_growth():
    code, result = invoke(
        "disclosure",
        {
            "previous": {
                "text": "收入稳定\n净利润亏损",
                "source": "https://example.com/old",
                "period": "2024",
                "metrics": [{"name": "net_income", "value": -100, "unit": "CNY", "basis": "annual"}],
            },
            "current": {
                "text": "收入稳定\n净利润扭亏",
                "source": "https://example.com/new",
                "period": "2025",
                "metrics": [{"name": "net_income", "value": 50, "unit": "CNY", "basis": "annual"}],
            },
        },
    )
    assert code == 0
    change = result["text_changes"][0]
    assert change["current"]["lines"] == [2, 2]
    assert change["current"]["text"] == "净利润扭亏"
    assert result["metric_changes"]["net_income"]["delta"] == 150
    assert result["metric_changes"]["net_income"]["growth_pct"] is None


def test_snapshot_missing_is_distinct_from_deleted_and_recovered():
    code, result = invoke(
        "snapshot",
        {"previous": {"price": 10, "roe": None, "old": True}, "current": {"price": None, "roe": 5, "new": True}},
    )
    assert code == 0
    assert {row["path"]: row["status"] for row in result["changes"]} == {
        "/price": "unavailable",
        "/roe": "recovered",
        "/old": "removed",
        "/new": "added",
    }


def test_event_study_maps_weekend_and_uses_pre_event_close():
    code, result = invoke(
        "event_study",
        {
            "event_dates": ["2026-01-03"],
            "horizons": [0, 1],
            "price_basis": "adjusted",
            "prices": [
                {"date": d, "close": v} for d, v in [("2026-01-02", 100), ("2026-01-05", 110), ("2026-01-06", 121)]
            ],
            "benchmark_prices": [
                {"date": d, "close": v} for d, v in [("2026-01-02", 100), ("2026-01-05", 102), ("2026-01-06", 104)]
            ],
        },
    )
    assert code == 0
    event = result["events"][0]
    assert event["effective_date"] == "2026-01-05"
    assert event["windows"][1]["excess_return_pct"] == pytest.approx(17)
    assert result["summary"]["1"]["samples"] == 1


def test_event_missing_benchmark_does_not_shift_event_date():
    code, result = invoke(
        "event_study",
        {
            "event_dates": ["2026-01-05"],
            "horizons": [0],
            "price_basis": "adjusted",
            "prices": [
                {"date": d, "close": v} for d, v in [("2026-01-02", 100), ("2026-01-05", 110), ("2026-01-06", 121)]
            ],
            "benchmark_prices": [{"date": "2026-01-02", "close": 100}, {"date": "2026-01-06", "close": 104}],
        },
    )
    assert code == 0
    assert result["events"][0]["effective_date"] == "2026-01-05"
    assert result["events"][0]["windows"][0]["excess_return_pct"] is None


def test_robustness_selects_only_on_training_and_tests_later_dates(make_kline_data):
    bars = make_kline_data(120)
    for bar in bars:
        bar["date"] = bar["date"].strftime("%Y-%m-%d")
        bar["open"] = min(max(bar["open"], bar["low"]), bar["high"])
    code, result = invoke(
        "robustness",
        {
            "datasets": [{"symbol": "AAPL", "bars": bars, "price_basis": "adjusted"}],
            "strategies": [
                {
                    "name": "hold",
                    "strategy": {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]}},
                },
                {
                    "name": "cash",
                    "strategy": {"entry": {"conditions": [{"indicator": "close", "operator": "<", "value": 0}]}},
                },
            ],
            "train_bars": 60,
            "test_bars": 30,
            "capital": 10000,
        },
    )
    assert code == 0
    folds = result["datasets"][0]["folds"]
    assert len(folds) == 2
    assert folds[0]["selected"] == "hold"
    assert folds[0]["train_end"] < folds[0]["test_start"]
    assert folds[0]["test_end"] < folds[1]["test_start"]
    assert {r["name"] for r in result["datasets"][0]["sensitivity"]} == {"hold", "cash"}


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"equity": True},
        {"equity": 100, "cash": 100, "entry_price": 10, "stop_price": 10, "risk_fraction": 0.01, "lot_size": 1},
        {"equity": 100, "cash": 100, "entry_price": 10, "stop_price": 9, "risk_fraction": 0.01, "lot_size": 0.5},
    ],
)
def test_invalid_position_inputs_are_json_errors(payload):
    code, result = invoke("position_size", payload)
    assert code == 1 and "error" in result


def test_zero_budget_never_rounds_up_to_a_lot():
    code, result = invoke(
        "position_size",
        {"equity": 1000, "cash": 1000, "entry_price": 100, "stop_price": 90, "risk_fraction": 0.001, "lot_size": 100},
    )
    assert code == 0 and result["quantity"] == 0


def test_nonfinite_snapshot_is_rejected_instead_of_disguised_as_missing():
    code, result = invoke("snapshot", {"previous": {"price": 10}, "current": {"price": float("nan")}})
    assert code == 1 and "error" in result


def test_json_exponent_overflow_is_rejected():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot"],
        input='{"previous":{"price":1},"current":{"price":1e400}}',
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 1 and "error" in json.loads(result.stdout)


def test_lone_surrogate_is_a_clean_error_without_traceback():
    code, result = invoke("snapshot", {"previous": {}, "current": {"text": "\ud800"}})
    assert code == 1 and "error" in result


def test_portfolio_arithmetic_overflow_is_an_error():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "AAPL", "quantity": 1e200, "price": 1e200, "currency": "USD"}],
        },
    )
    assert code == 1 and "error" in result


def test_disclosure_arithmetic_overflow_is_an_error():
    def document(value):
        return {
            "text": "正文",
            "source": "fixture",
            "period": "2025",
            "metrics": [{"name": "profit", "value": value, "unit": "USD", "basis": "annual"}],
        }

    code, result = invoke("disclosure", {"previous": document(-1e308), "current": document(1e308)})
    assert code == 1 and "error" in result


def test_portfolio_correlations_align_common_dates():
    dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": s, "price": 100, "quantity": 1, "currency": "USD"} for s in ["AAPL", "MSFT"]],
            "histories": [
                {
                    "symbol": s,
                    "prices": [
                        {"date": d, "close": v * scale} for d, v in zip(dates, [100, 110, 99, 120], strict=True)
                    ],
                }
                for s, scale in [("AAPL", 1), ("MSFT", 2)]
            ],
        },
    )
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 3


def test_peer_reporting_periods_must_match():
    code, result = invoke(
        "peers",
        {
            "companies": [
                {
                    "symbol": s,
                    "period": period,
                    "metrics": [{"name": "roe", "unit": "percent", "basis": "annual", "value": value}],
                }
                for s, period, value in [("A", "2024", 10), ("B", "2025", 20)]
            ]
        },
    )
    assert code == 0 and result["metrics"]["roe"]["ascending"] is None


def test_disclosure_incompatible_units_have_no_delta():
    def document(unit):
        return {
            "text": "正文",
            "source": "fixture",
            "period": "2025",
            "metrics": [{"name": "profit", "value": 10, "unit": unit, "basis": "annual"}],
        }

    code, result = invoke("disclosure", {"previous": document("USD"), "current": document("CNY")})
    assert code == 0 and result["metric_changes"]["profit"]["delta"] is None


def test_snapshot_nested_errors_are_unavailable_with_escaped_paths():
    code, result = invoke(
        "snapshot",
        {
            "previous": {"a/b": {"~price": {"value": 10}}},
            "current": {"a/b": {"~price": {"error": "source unavailable"}}},
        },
    )
    assert code == 0
    assert result["changes"][0]["path"] == "/a~1b/~0price"
    assert result["changes"][0]["status"] == "unavailable"


def test_event_overlapping_windows_are_marked():
    code, result = invoke(
        "event_study",
        {
            "event_dates": ["2026-01-05", "2026-01-06"],
            "horizons": [0, 1],
            "price_basis": "adjusted",
            "prices": [{"date": d, "close": 100} for d in ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]],
            "benchmark_prices": [
                {"date": d, "close": 100} for d in ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
            ],
        },
    )
    assert code == 0 and all(e["overlaps_other_event"] for e in result["events"])


def test_adjacent_event_returns_are_not_overlapping():
    payload = {"event_dates": ["2026-01-05", "2026-01-06"], "horizons": [0], "price_basis": "adjusted"}
    payload["prices"] = [{"date": d, "close": 100} for d in ["2026-01-02", "2026-01-05", "2026-01-06"]]
    payload["benchmark_prices"] = payload["prices"]
    code, result = invoke("event_study", payload)
    assert code == 0
    assert not any(e["overlaps_other_event"] for e in result["events"])


def test_large_input_file_round_trip(tmp_path):
    path = tmp_path / "输入.json"
    path.write_text(
        json.dumps({"previous": {"text": "a" * 100000}, "current": {"text": "b" * 100000}}), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot", "--input", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0 and json.loads(result.stdout)["changes"][0]["status"] == "changed"


def test_invalid_cli_arguments_still_emit_json():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "unknown"], capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode != 0
    assert "error" in json.loads(result.stdout)
    assert result.stderr == ""


def test_robustness_invalid_indicator_is_a_failed_candidate(make_kline_data):
    bars = make_kline_data(60)
    for bar in bars:
        bar["date"] = bar["date"].strftime("%Y-%m-%d")
        bar["open"] = bar["close"]
    code, result = invoke(
        "robustness",
        {
            "datasets": [{"symbol": "AAPL", "bars": bars, "price_basis": "adjusted"}],
            "strategies": [
                {
                    "name": "typo",
                    "strategy": {"entry": {"conditions": [{"indicator": "clsoe", "operator": ">", "value": 0}]}},
                }
            ],
            "train_bars": 30,
            "test_bars": 30,
        },
    )
    assert code == 0
    fold = result["datasets"][0]["folds"][0]
    assert fold["selected"] is None
    assert "error" in fold["candidates"][0]


@pytest.mark.parametrize(
    "invalid",
    [
        {"entry": {"conditions": [{"indicator": "close", "operator": "gt", "value": 0}]}},
        {"entry": {"logic": "al", "conditions": [{"indicator": "close", "operator": ">", "value": 0}]}},
        {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]}, "exit": {"stop_loss": "nan"}},
        {"entry": []},
        {"simulation": []},
        {"parameters": None},
        {"position": []},
        {"entry": {"conditions": [{"indicator": None, "operator": ">", "value": 0}]}},
        {
            "entry": {"conditions": [{"indicator": "close", "operator": "<", "value": 0}]},
            "exit": {"conditions": [{"indicator": "clsoe", "operator": ">", "value": 0}]},
        },
        {
            "entry": {"conditions": [{"indicator": "close", "operator": "<", "value": 0}]},
            "exit": {"conditions": [{"indicator": "ma", "period": "bad", "operator": ">", "value": 0}]},
        },
    ],
)
def test_malformed_strategy_cannot_win_as_a_cash_candidate(make_kline_data, invalid):
    bars = make_kline_data(60, trend="down")
    for bar in bars:
        bar["date"] = bar["date"].strftime("%Y-%m-%d")
        bar["open"] = bar["close"]
    code, result = invoke(
        "robustness",
        {
            "datasets": [{"symbol": "AAPL", "bars": bars, "price_basis": "adjusted"}],
            "strategies": [
                {"name": "invalid", "strategy": invalid},
                {
                    "name": "hold",
                    "strategy": {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]}},
                },
            ],
            "train_bars": 30,
            "test_bars": 30,
        },
    )
    assert code == 0
    fold = result["datasets"][0]["folds"][0]
    assert fold["selected"] == "hold"
    assert "error" in fold["candidates"][0]


def test_training_selection_uses_actual_equity_not_rounded_return():
    fixture = json.loads((Path(__file__).parent / "fixtures/stateless_analysis.json").read_text(encoding="utf-8"))
    payload = fixture["validate_backtest"]["args"]
    for i, bar in enumerate(payload["datasets"][0]["bars"]):
        bar.update({field: 100 + i * 0.001 for field in ["open", "high", "low", "close"]})
    payload["strategies"] = [
        {
            "name": name,
            "strategy": {
                "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]},
                "position": {"size": size},
                "simulation": {"commission": 0, "slippage": 0},
            },
        }
        for name, size in [("lower", 0.50), ("higher", 0.51)]
    ]
    code, result = invoke("robustness", payload)
    assert code == 0
    fold = result["datasets"][0]["folds"][0]
    assert fold["candidates"][0]["metrics"]["total_return"] == fold["candidates"][1]["metrics"]["total_return"]
    assert fold["selected"] == "higher"


def test_undefined_backtest_ratio_remains_null_without_rejecting_valid_run():
    fixture = json.loads((Path(__file__).parent / "fixtures/stateless_analysis.json").read_text(encoding="utf-8"))
    payload = fixture["validate_backtest"]["args"]
    for bar in payload["datasets"][0]["bars"]:
        bar.update({field: 100 for field in ["open", "high", "low", "close"]})
    strategy = payload["strategies"][0]["strategy"]
    strategy["exit"] = {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]}
    strategy["simulation"] = {"commission": 0, "slippage": 0}
    code, result = invoke("robustness", payload)
    assert code == 0
    candidate = result["datasets"][0]["folds"][0]["candidates"][0]
    assert candidate["metrics"]["total_trades"] > 0
    assert candidate["metrics"]["profit_loss_ratio"] is None


@pytest.mark.parametrize("command", ["event_study", "robustness"])
def test_symbol_based_cli_reuses_python_data_collection(command, make_kline_data, monkeypatch, capsys):
    bars = make_kline_data(60)
    for bar in bars:
        bar["date"] = bar["date"].strftime("%Y-%m-%d")
        bar["open"] = bar["close"]

    def provider_process(argv, **kwargs):
        assert Path(argv[1]).name == "stock_data.py" and argv[2] == "kline"
        return SimpleNamespace(returncode=0, stdout=json.dumps(bars), stderr="")

    payload = {"start_date": bars[0]["date"], "end_date": bars[-1]["date"], "price_basis": "adjusted"}
    if command == "event_study":
        payload.update({"symbol": "AAPL", "benchmark": "SPY", "event_dates": [bars[10]["date"]], "horizons": [0, 1]})
    else:
        payload.update(
            {
                "symbols": ["AAPL"],
                "train_bars": 30,
                "test_bars": 30,
                "strategies": [
                    {
                        "name": "hold",
                        "strategy": {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]}},
                    }
                ],
            }
        )
    monkeypatch.setattr(subprocess, "run", provider_process)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), command])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exited.value.code == 0
    result = json.loads(capsys.readouterr().out)
    if command == "event_study":
        assert result["events"][0]["windows"][0]["excess_return_pct"] == pytest.approx(0)
    else:
        assert len(result["datasets"][0]["folds"]) == 1
