import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/bull-trend/scripts/tail_analysis.py"


def run_cli(tmp_path, command, data, *args):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), command, str(source), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result, json.loads(result.stdout)


def test_gaussian_kappa_and_historical_percentile(tmp_path):
    returns = np.random.default_rng(12).normal(size=20000).tolist()
    args = ("--n", "2", "10", "--draws", "20000", "--seed", "42", "--current", "100")
    result, output = run_cli(tmp_path, "returns", returns, *args)
    assert result.returncode == 0
    assert output["sample_count"] == 20000
    assert output["current_event"]["percentile"] == 1
    assert output["current_event"]["upper_exceedances"] == 0
    assert all(abs(row["kappa"]) < 0.06 for row in output["kappa"])
    _, repeated = run_cli(tmp_path, "returns", returns, *args)
    assert output == repeated


def test_winner_contribution_uses_closed_monetary_pnl(tmp_path):
    trades = [{"type": "buy", "amount": 50000}]
    trades += [{"type": "sell", "pnl": value, "pnl_pct": 0.9} for value in (100, 50, 25, -75)]
    result, output = run_cli(tmp_path, "trades", {"trades": trades})
    assert result.returncode == 0
    assert output["closed_trades"] == 4
    assert output["gross_profit"] == 175
    assert output["gross_loss"] == -75
    assert output["realized_net_pnl"] == 100
    assert output["worst_trade_pnl"] == -75
    assert output["winner_contribution"][0]["share_of_gross_profit"] == pytest.approx(0.5714285714)
    assert output["winner_contribution"][0]["realized_pnl_without_top_k"] == 0
    assert output["winner_contribution"][1]["realized_pnl_without_top_k"] == -75
    assert output["win_rate"] == 0.75
    assert output["payoff_ratio"] == pytest.approx(0.7777777778)
    assert output["breakeven_win_rate"] == pytest.approx(0.5625)


def test_return_asymmetry_compares_tail_means_and_omega(tmp_path):
    returns = [-0.05, -0.01, -0.01, 0.0, 0.01, 0.01, 0.02, 0.15]
    result, output = run_cli(tmp_path, "returns", returns, "--draws", "100", "--tail-prob", "0.125")
    assert result.returncode == 0
    asymmetry = output["asymmetry"]
    assert asymmetry["lower_quantile"] == pytest.approx(-0.015)
    assert asymmetry["upper_quantile"] == pytest.approx(0.03625)
    assert asymmetry["expected_tail_loss"] == pytest.approx(-0.05)
    assert asymmetry["expected_tail_gain"] == pytest.approx(0.15)
    assert asymmetry["tail_gain_loss_ratio"] == pytest.approx(3.0)
    assert asymmetry["lower_tail_count"] == asymmetry["upper_tail_count"] == 1
    assert asymmetry["omega"] == pytest.approx(0.19 / 0.07)


def test_return_asymmetry_without_losses_is_unavailable(tmp_path):
    result, output = run_cli(tmp_path, "returns", [0.01, 0.02, 0.03], "--draws", "100")
    assert result.returncode == 0
    assert output["asymmetry"]["omega"] is None
    assert output["asymmetry"]["tail_gain_loss_ratio"] is None


@pytest.mark.parametrize(
    ("pnl", "shape", "h_small", "ratio_small"),
    [
        ([-5, -5, 0, 10, 25], "convex", 2.5, 2.0),
        ([-30, -10, 0, 2, 2], "concave", -4.0, 0.2),
        ([-2, -1, 0, 1, 2], "linear", 0.0, 1.0),
    ],
)
def test_fragility_uses_symmetric_scenario_pairs(tmp_path, pnl, shape, h_small, ratio_small):
    scenarios = [{"shock": shock, "pnl": value} for shock, value in zip([-0.2, -0.1, 0, 0.1, 0.2], pnl, strict=True)]
    result, output = run_cli(tmp_path, "fragility", {"scenarios": scenarios, "capital": 100})
    assert result.returncode == 0
    small, large = output["pairs"]
    assert small["delta"] == pytest.approx(0.1) and large["delta"] == pytest.approx(0.2)
    assert small["h"] == pytest.approx(h_small)
    assert small["h_over_capital"] == pytest.approx(h_small / 100)
    assert small["gain_loss_ratio"] == pytest.approx(ratio_small)
    assert small["shape"] == shape


def test_fragility_can_center_on_a_stress_level(tmp_path):
    scenarios = [{"shock": shock, "pnl": pnl} for shock, pnl in ((-0.3, -60), (-0.2, -30), (-0.1, -10), (0, 0))]
    result, output = run_cli(tmp_path, "fragility", {"scenarios": scenarios}, "--center", "-0.2")
    assert result.returncode == 0
    [pair] = output["pairs"]
    assert pair["h"] == pytest.approx(-5.0)
    assert pair["h_over_capital"] is None


def test_zero_dispersion_is_unavailable(tmp_path):
    result, output = run_cli(tmp_path, "returns", [0, 0, 0])
    assert result.returncode == 0
    assert all(row["kappa"] is None and row["unavailable_reason"] for row in output["kappa"])


def test_student_t_has_slower_aggregation_than_gaussian(tmp_path):
    returns = np.random.default_rng(12).standard_t(df=3, size=20000).tolist()
    result, output = run_cli(tmp_path, "returns", returns, "--n", "2", "--draws", "40000")
    assert result.returncode == 0
    assert output["kappa"][0]["kappa"] > 0.15


@pytest.mark.parametrize("contents", [None, "{malformed"])
def test_unreadable_or_invalid_json_is_a_clean_error(tmp_path, contents):
    source = tmp_path / "bad.json"
    if contents is not None:
        source.write_text(contents, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "returns", str(source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]
    assert "Traceback" not in result.stderr


def test_no_winners_does_not_divide_by_zero(tmp_path):
    result, output = run_cli(tmp_path, "trades", {"trades": [{"type": "sell", "pnl": -50}]})
    assert result.returncode == 0
    assert output["winner_contribution"][0]["share_of_gross_profit"] is None
    assert output["winner_contribution"][1]["actual_k"] == 0


@pytest.mark.parametrize(
    ("command", "data", "args"),
    [
        ("returns", [0.1], []),
        ("returns", [0.1, float("nan")], []),
        ("returns", [True, 0.1], []),
        ("returns", [0.1, 0.2], ["--n", "1"]),
        ("returns", [0.1, 0.2], ["--current", "inf"]),
        ("trades", {"trades": [{"type": "sell"}]}, []),
        ("trades", {"trades": [None]}, []),
        ("returns", [0.1, 0.2], ["--tail-prob", "0.5"]),
        ("fragility", {"scenarios": [{"shock": 0.1, "pnl": 1}, {"shock": -0.1, "pnl": -1}]}, []),
        ("fragility", {"scenarios": [{"shock": 0, "pnl": 0}, {"shock": 0.1, "pnl": 1}]}, []),
        ("fragility", {"scenarios": [{"shock": 0, "pnl": 0}, {"shock": 0, "pnl": 1}]}, []),
        ("fragility", {"scenarios": [{"shock": 0, "pnl": 0}], "capital": 0}, []),
    ],
)
def test_bad_input_returns_json_error(tmp_path, command, data, args):
    result, output = run_cli(tmp_path, command, data, *args)
    assert result.returncode == 1
    assert output["error"]
    assert "Traceback" not in result.stderr
