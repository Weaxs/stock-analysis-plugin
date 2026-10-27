import io
import json
import runpy
import subprocess
import sys
import time
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "stateless_analysis.py"


def invoke(command, payload, exact=False):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert not result.stderr, result.stderr
    if exact:
        return result.returncode, json.loads(result.stdout, parse_int=exact_json_number, parse_float=exact_json_number)
    return result.returncode, json.loads(result.stdout)


def exact_json_number(text):
    return Fraction(Decimal(text))


def invoke_raw(raw, exact=True, command="rebalance", timeout=30):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), command],
        input=raw,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    assert not result.stderr, result.stderr
    if exact:
        return result.returncode, json.loads(result.stdout, parse_int=exact_json_number, parse_float=exact_json_number)
    return result.returncode, json.loads(result.stdout, parse_int=str, parse_float=str)


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


def test_portfolio_stress_scenario_composes_asset_and_fx_shocks():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "CNY",
            "cash": 300,
            "fx_rates": [{"currency": "USD", "rate": 7}],
            "positions": [
                {"symbol": "AAPL", "quantity": 1, "price": 100, "currency": "USD", "sector": "tech"},
                {"symbol": "600519", "quantity": 10, "price": 100, "currency": "CNY", "sector": "consumer"},
            ],
            "stress_scenarios": [
                {
                    "name": "risk_off",
                    "market_shock": -0.1,
                    "sector_shocks": [{"sector": "tech", "shock": -0.2}],
                    "symbol_shocks": [{"symbol": "600519", "shock": 0.05}],
                    "currency_shocks": [{"currency": "USD", "shock": -0.1}],
                }
            ],
        },
    )
    assert code == 0
    scenario = result["stress_tests"][0]
    assert scenario["equity_before"] == 2000
    assert scenario["equity_after"] == pytest.approx(1854)
    assert scenario["pnl_base"] == pytest.approx(-146)
    assert scenario["return_pct"] == pytest.approx(-7.3)
    assert scenario["positions"][0] == {
        "symbol": "AAPL",
        "asset_shock": -0.2,
        "fx_shock": -0.1,
        "value_before": 700,
        "value_after": 504,
        "pnl_base": -196,
    }


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


def test_rebalance_sells_before_buys_and_reconciles_cash_and_weights():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 100,
            "positions": [
                {"symbol": "AAA", "quantity": 10, "price": 10, "currency": "USD", "sector": "tech"},
                {"symbol": "BBB", "quantity": 10, "price": 10, "currency": "USD", "sector": "health"},
            ],
            "targets": [
                {"symbol": "AAA", "weight": 0.2, "lot_size": 1},
                {"symbol": "BBB", "weight": 0.4, "lot_size": 1},
                {"symbol": "CCC", "weight": 0.2, "price": 10, "currency": "USD", "sector": "tech", "lot_size": 1},
            ],
        },
    )
    assert code == 0
    assert [(o["side"], o["symbol"], o["quantity"]) for o in result["orders"]] == [
        ("sell", "AAA", 4),
        ("buy", "BBB", 2),
        ("buy", "CCC", 6),
    ]
    assert result["initial_equity"] == 300
    assert result["cash_after"] == 60
    assert result["equity_after"] == 300
    assert result["cash_weight_after"] == pytest.approx(0.2)
    assert result["gross_turnover_fraction"] == pytest.approx(0.4)
    assert result["estimated_cost_base"] == 0
    assert not result["cash_limited"]
    assert {p["symbol"]: p["weight"] for p in result["positions_after"]} == {
        "AAA": pytest.approx(0.2),
        "BBB": pytest.approx(0.4),
        "CCC": pytest.approx(0.2),
    }


def test_rebalance_uses_target_price_consistently_for_initial_and_after_equity():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 1000,
            "positions": [{"symbol": "AAA", "quantity": 10, "price": 100, "currency": "USD"}],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 200, "lot_size": 1}],
        },
    )
    assert code == 0
    assert result["initial_equity"] == 3000
    assert result["equity_after"] == 3000
    assert result["cash_after"] == 0
    assert result["estimated_cost_base"] == 0
    assert [(o["side"], o["symbol"], o["quantity"]) for o in result["orders"]] == [("buy", "AAA", 5)]
    assert result["positions_after"][0]["weight"] == 1
    assert result["unfilled"] == []


def test_rebalance_scales_buys_for_cash_after_costs_without_overspending():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 50,
            "positions": [{"symbol": "AAA", "quantity": 10, "price": 10, "currency": "USD"}],
            "targets": [
                {"symbol": "AAA", "weight": 0},
                {"symbol": "BBB", "weight": 1, "price": 10, "currency": "USD", "lot_size": 1},
            ],
            "fee_rate": 0.01,
            "slippage_rate": 0.01,
        },
    )
    assert code == 0
    assert result["cash_limited"]
    assert [(o["side"], o["symbol"], o["quantity"]) for o in result["orders"]] == [
        ("sell", "AAA", 10),
        ("buy", "BBB", 14),
    ]
    assert result["unfilled"] == [{"symbol": "BBB", "side": "buy", "quantity": 1, "reason": "cash"}]
    assert result["cash_after"] == pytest.approx(5.196)
    assert result["equity_after"] == pytest.approx(145.196)
    assert result["estimated_cost_base"] == pytest.approx(4.804)
    assert result["gross_turnover_fraction"] == pytest.approx(1.6)
    assert result["positions_after"][0]["quantity"] == 14


def test_rebalance_cash_scaling_preserves_exact_cost_and_residuals():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 6.05,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 1, "currency": "USD", "lot_size": 1}],
            "fee_rate": 0.1,
            "slippage_rate": 0.1,
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 5)]
    assert result["cash_after"] == 0
    assert result["cash_limited"]
    assert result["unfilled"] == [
        {"symbol": "AAA", "side": "buy", "quantity": pytest.approx(0.05), "reason": "lot_rounding"},
        {"symbol": "AAA", "side": "buy", "quantity": 1, "reason": "cash"},
    ]
    assert result["positions_after"][0]["weight"] == pytest.approx(1)


def test_rebalance_supports_cash_only_fx_and_lot_rounding():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "CNY",
            "cash": 10000,
            "positions": [],
            "fx_rates": [{"currency": "USD", "rate": 7}],
            "targets": [{"symbol": "AAPL", "weight": 0.5, "price": 100, "currency": "USD", "lot_size": 2}],
        },
    )
    assert code == 0
    assert result["orders"][0]["side"] == "buy"
    assert result["orders"][0]["quantity"] == 6
    assert result["cash_after"] == 5800
    assert result["positions_after"][0]["weight"] == pytest.approx(0.42)
    assert result["gross_turnover_fraction"] == pytest.approx(0.42)


def test_rebalance_reports_target_lot_rounding_shortfall():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 150,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 10, "currency": "USD", "lot_size": 10}],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 10)]
    assert result["unfilled"] == [{"symbol": "AAA", "side": "buy", "quantity": 5, "reason": "lot_rounding"}]


def test_rebalance_floors_partial_sell_from_raw_delta():
    raw = '{"base_currency":"USD","cash":0,"positions":[{"symbol":"A","quantity":10,"price":1,"currency":"USD"}],"targets":[{"symbol":"A","weight":0.95,"lot_size":1}]}'
    code, result = invoke_raw(raw)
    assert code == 0
    assert result["orders"] == []
    assert result["positions_after"][0]["quantity"] == 10
    assert result["positions_after"][0]["weight"] == 1
    assert result["unfilled"] == [{"symbol": "A", "side": "sell", "quantity": Fraction(1, 2), "reason": "lot_rounding"}]


def test_rebalance_separates_lot_and_cash_shortfalls_without_double_counting():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 110,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 10, "currency": "USD", "lot_size": 10}],
            "fee_rate": 0.1,
            "slippage_rate": 0.1,
        },
    )
    assert code == 0
    assert result["orders"] == []
    assert result["unfilled"] == [
        {"symbol": "AAA", "side": "buy", "quantity": 1, "reason": "lot_rounding"},
        {"symbol": "AAA", "side": "buy", "quantity": 10, "reason": "cash"},
    ]
    assert result["cash_after"] == 110


def test_rebalance_does_not_round_down_exact_lot_boundary_from_float_error():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 0.3,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 0.1, "currency": "USD", "lot_size": 1}],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 3)]
    assert result["cash_after"] == 0
    assert result["unfilled"] == []
    assert result["positions_after"][0]["weight"] == pytest.approx(1)


def test_rebalance_ignores_float_residue_above_exact_lot_boundary():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 0.07,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 0.01, "currency": "USD", "lot_size": 1}],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 7)]
    assert result["unfilled"] == []
    assert result["positions_after"][0]["weight"] == pytest.approx(1)


def test_rebalance_never_rounds_up_past_cash_at_large_lot_boundary():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 29999999.999995,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 100000, "currency": "USD", "lot_size": 100}],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 200)]
    assert result["cash_after"] == pytest.approx(9999999.999995)
    assert not result["cash_limited"]
    assert result["unfilled"] == [
        {"symbol": "AAA", "side": "buy", "quantity": pytest.approx(100), "reason": "lot_rounding"}
    ]


def test_rebalance_preserves_quantity_precision_for_extreme_price_and_cash():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 441330946.461989,
            "positions": [],
            "targets": [
                {
                    "symbol": "AAA",
                    "weight": 0.5810639296577244,
                    "price": 1.2455774399828766e-07,
                    "currency": "USD",
                    "lot_size": 1,
                }
            ],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 2058816142610061)]
    assert result["unfilled"] == [
        {"symbol": "AAA", "side": "buy", "quantity": pytest.approx(0.2635659309158), "reason": "lot_rounding"}
    ]


def test_rebalance_rejects_target_weights_above_one_even_within_old_tolerance():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 100,
            "positions": [],
            "targets": [
                {"symbol": "A", "weight": 0.5000000004, "price": 1, "currency": "USD", "lot_size": 1},
                {"symbol": "B", "weight": 0.5000000004, "price": 1, "currency": "USD", "lot_size": 1},
            ],
        },
    )
    assert code == 1 and "error" in result


def test_rebalance_scales_even_smallest_overspend_instead_of_hiding_it():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 100,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 100, "currency": "USD", "lot_size": 1}],
            "fee_rate": 0.000000000005,
        },
    )
    assert code == 0
    assert result["cash_limited"]
    assert result["orders"] == []
    assert result["unfilled"] == [{"symbol": "AAA", "side": "buy", "quantity": 1, "reason": "cash"}]
    assert result["cash_after"] == 100


def test_rebalance_accepts_lot_size_above_legacy_million_cap():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 1000001,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 1, "currency": "USD", "lot_size": 1000001}],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 1000001)]
    assert result["cash_after"] == 0


def test_rebalance_supports_decimal_values_beyond_default_context_precision():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 1e30,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 6, "currency": "USD", "lot_size": 1}],
        },
    )
    assert code == 0
    assert result["initial_equity"] == pytest.approx(1e30)
    assert result["equity_after"] == pytest.approx(1e30)
    assert result["cash_after"] == 4
    assert result["positions_after"][0]["value_base"] == pytest.approx(1e30 - 4)
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 166666666666666666666666666666)]
    assert result["unfilled"] == [
        {"symbol": "AAA", "side": "buy", "quantity": pytest.approx(2 / 3), "reason": "lot_rounding"}
    ]


def test_rebalance_uses_exact_fx_unit_value_for_cash_decisions():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "CNY",
            "cash": 0.3,
            "positions": [],
            "fx_rates": [{"currency": "USD", "rate": 3}],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 0.1, "currency": "USD", "lot_size": 1}],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 1)]
    assert result["cash_after"] == 0
    assert not result["cash_limited"]
    assert result["unfilled"] == []


def test_rebalance_computes_actual_weight_before_display_rounding():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "AAA", "quantity": 1, "price": 0.0000001, "currency": "USD"}],
            "targets": [{"symbol": "AAA", "weight": 1, "lot_size": 1}],
        },
    )
    assert code == 0
    assert result["positions_after"][0]["weight"] == pytest.approx(1)
    assert result["positions_after"][0]["value_base"] == pytest.approx(0.0000001)


def test_rebalance_reports_small_but_real_lot_residual():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 1.00000000005,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 1, "currency": "USD", "lot_size": 1}],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 1)]
    assert result["unfilled"] == [
        {"symbol": "AAA", "side": "buy", "quantity": pytest.approx(0.00000000005), "reason": "lot_rounding"}
    ]


def test_rebalance_preserves_tiny_fee_for_cash_limit_decision():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 100,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 100, "currency": "USD", "lot_size": 1}],
            "fee_rate": 0.00000000000000000000000000005,
        },
    )
    assert code == 0
    assert result["cash_limited"]
    assert result["orders"] == []
    assert result["unfilled"] == [{"symbol": "AAA", "side": "buy", "quantity": 1, "reason": "cash"}]
    assert result["cash_after"] == 100


def test_rebalance_uses_exact_rational_floor_for_extreme_ratio():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 1000000000000000,
            "positions": [],
            "targets": [{"symbol": "AAA", "weight": 1, "price": 0.0000000000006, "currency": "USD", "lot_size": 1}],
        },
    )
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in result["orders"]] == [("buy", 1666666666666666666666666666)]
    assert result["cash_after"] == pytest.approx(0.0000000000004)
    assert result["unfilled"] == [
        {"symbol": "AAA", "side": "buy", "quantity": pytest.approx(2 / 3), "reason": "lot_rounding"}
    ]


def test_rebalance_rejects_exact_json_weight_sum_above_one():
    raw = (
        '{"base_currency":"USD","cash":100,"positions":[],"targets":['
        '{"symbol":"A","weight":0.50000000000000000001,"price":1,"currency":"USD","lot_size":1},'
        '{"symbol":"B","weight":0.5,"price":1,"currency":"USD","lot_size":1}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 1
    assert "error" in out


def test_rebalance_preserves_fractional_current_quantity_exactly():
    raw = (
        '{"base_currency":"USD","cash":1.99999999999999999999,"positions":['
        '{"symbol":"A","quantity":1e-20,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":8,"price":1,"currency":"USD"}],'
        '"targets":[{"symbol":"A","weight":0.2,"lot_size":1}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert [(o["side"], o["symbol"], o["quantity"]) for o in out["orders"]] == [("sell", "B", 8), ("buy", "A", 1)]
    assert out["unfilled"] == [
        {"symbol": "A", "side": "buy", "quantity": 1 - Fraction("1e-20"), "reason": "lot_rounding"}
    ]
    assert out["positions_after"][0]["quantity"] == 1 + Fraction("1e-20")
    assert out["positions_after"][0]["value_base"] == 1 + Fraction("1e-20")


@pytest.mark.parametrize(
    "raw",
    [
        '{"base_currency":"USD","cash":1,"positions":[],"targets":[{"symbol":"A","weight":-1e-400,"price":1,"currency":"USD","lot_size":1}]}',
        '{"base_currency":"USD","cash":1,"positions":[],"targets":[{"symbol":"A","weight":1,"price":1,"currency":"USD","lot_size":1.00000000000000000001}]}',
    ],
)
def test_rebalance_rejects_exact_raw_number_validation(raw):
    code, out = invoke_raw(raw)
    assert code == 1
    assert "error" in out


def test_rebalance_preserves_large_integer_equity_and_position_value():
    raw = (
        '{"base_currency":"USD","cash":1000000000000000000000000000000,"positions":['
        '{"symbol":"A","quantity":4,"price":1,"currency":"USD"}],'
        '"targets":[{"symbol":"A","weight":1,"lot_size":1}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == 1000000000000000000000000000004
    assert out["positions_after"][0]["value_base"] == 1000000000000000000000000000004


def test_rebalance_accepts_large_integer_lot_without_float_truncation():
    raw = (
        '{"base_currency":"USD","cash":9007199254740993,"positions":[],"targets":['
        '{"symbol":"AAA","weight":1,"price":1,"currency":"USD","lot_size":9007199254740993.0}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert [(o["side"], o["quantity"]) for o in out["orders"]] == [("buy", 9007199254740993)]
    assert out["cash_after"] == 0


def test_rebalance_full_exit_preserves_large_held_quantity_exactly():
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"AAA","quantity":9007199254740993.0,"price":1,"currency":"USD"}],'
        '"targets":[{"symbol":"AAA","weight":0}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["orders"][0]["side"] == "sell"
    assert out["orders"][0]["quantity"] == 9007199254740993
    assert out["orders"][0]["estimated_cash_flow_base"] == 9007199254740993
    assert out["positions_after"] == []


def test_rebalance_preserves_large_fractional_cash_exactly():
    raw = (
        '{"base_currency":"USD","cash":9007199254740993.5,"positions":[],'
        '"targets":[{"symbol":"A","weight":0,"price":1,"currency":"USD"}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == Fraction("9007199254740993.5")
    assert out["equity_after"] == Fraction("9007199254740993.5")
    assert out["cash_after"] == Fraction("9007199254740993.5")
    assert out["orders"] == []


def test_rebalance_preserves_extreme_large_fractional_cash_exactly():
    raw = (
        '{"base_currency":"USD","cash":1000000000000000000000000000000.5,"positions":[],'
        '"targets":[{"symbol":"A","weight":0,"price":1,"currency":"USD"}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == Fraction("1000000000000000000000000000000.5")
    assert out["equity_after"] == Fraction("1000000000000000000000000000000.5")
    assert out["cash_after"] == Fraction("1000000000000000000000000000000.5")
    assert out["orders"] == []


def test_rebalance_preserves_placeholder_like_symbols_as_strings():
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"__stock_exact_number_123__","quantity":1,"price":1,"currency":"USD","sector":"__stock_exact_number_1e999__"}],'
        '"targets":[{"symbol":"__stock_exact_number_123__","weight":0}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["orders"][0]["symbol"] == "__stock_exact_number_123__"
    assert out["positions_after"] == []


def test_rebalance_requires_lot_for_positive_raw_weight_underflow():
    raw = '{"base_currency":"USD","cash":1,"positions":[],"targets":[{"symbol":"A","weight":1e-400,"price":1,"currency":"USD"}]}'
    code, out = invoke_raw(raw)
    assert code == 1
    assert "error" in out


def test_rebalance_accepts_positive_raw_price_underflow():
    raw = '{"base_currency":"USD","cash":1,"positions":[],"targets":[{"symbol":"A","weight":1,"price":1e-400,"currency":"USD","lot_size":1}]}'
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["orders"][0]["side"] == "buy"
    assert not out["cash_limited"]


def test_rebalance_preserves_raw_price_precision_in_order_and_positions():
    raw = (
        '{"base_currency":"USD","cash":9007199254740993.0,"positions":[],"targets":['
        '{"symbol":"A","weight":1,"price":9007199254740993.0,"currency":"USD","lot_size":1}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["orders"][0]["reference_price"] == 9007199254740993
    assert out["positions_after"][0]["price"] == 9007199254740993


def test_rebalance_preserves_tiny_lot_residual_exactly():
    cash = "1." + "0" * 399 + "1"
    raw = f'{{"base_currency":"USD","cash":{cash},"positions":[],"targets":[{{"symbol":"A","weight":1,"price":1,"currency":"USD","lot_size":1}}]}}'
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["unfilled"] == [
        {"symbol": "A", "side": "buy", "quantity": Fraction(1, 10**400), "reason": "lot_rounding"}
    ]


def test_rebalance_rejects_exact_rate_above_ten_percent():
    for field in ("fee_rate", "slippage_rate"):
        raw = f'{{"base_currency":"USD","cash":1,"positions":[],"targets":[{{"symbol":"A","weight":0,"price":1,"currency":"USD"}}],"{field}":0.100000000000000005}}'
        code, out = invoke_raw(raw)
        assert code == 1 and "error" in out


def test_rebalance_supports_aggregate_equity_above_float_max():
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1e308,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1e308,"currency":"USD"}],'
        '"targets":[{"symbol":"A","weight":0},{"symbol":"B","weight":0}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == 2 * 10**308
    assert out["cash_after"] == 2 * 10**308
    assert [order["side"] for order in out["orders"]] == ["sell", "sell"]


def test_rebalance_supports_exact_fx_unit_below_float_min():
    raw = (
        '{"base_currency":"CNY","cash":1e-200,"fx_rates":[{"currency":"USD","rate":1e-200}],"positions":[],'
        '"targets":[{"symbol":"A","weight":1,"price":1e-200,"currency":"USD","lot_size":1}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["orders"][0]["quantity"] == 10**200
    assert out["positions_after"][0]["weight"] == pytest.approx(1)


def test_rebalance_rejects_tiny_positive_target_instead_of_liquidating():
    raw = (
        '{"base_currency":"USD","cash":0,"positions":[{"symbol":"A","quantity":1,"price":1,"currency":"USD"}],'
        '"targets":[{"symbol":"A","weight":1e-400,"lot_size":1}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 1 and "error" in out


def test_rebalance_requires_positions_field():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 0,
            "targets": [{"symbol": "A", "weight": 0, "price": 1, "currency": "USD"}],
        },
    )
    assert code == 1
    assert "error" in result


def test_rebalance_allows_zero_equity_noop():
    raw = '{"base_currency":"USD","cash":0,"positions":[],"targets":[{"symbol":"A","weight":0,"price":1,"currency":"USD"}]}'
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == 0
    assert out["equity_after"] == 0
    assert out["cash_after"] == 0
    assert out["cash_weight_after"] is None
    assert out["gross_turnover_fraction"] == 0
    assert out["estimated_cost_base"] == 0
    assert out["orders"] == []
    assert out["positions_after"] == []


def test_rebalance_accepts_raw_number_above_binary64_max():
    raw = '{"base_currency":"USD","cash":2e308,"positions":[],"targets":[{"symbol":"A","weight":0,"price":1,"currency":"USD"}]}'
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == 2 * 10**308
    assert out["cash_after"] == 2 * 10**308
    assert out["orders"] == []


def test_rebalance_accepts_integer_form_above_binary64_max():
    raw = (
        '{"base_currency":"USD","cash":'
        + "2"
        + "0" * 308
        + ',"positions":[],"targets":[{"symbol":"A","weight":0,"price":1,"currency":"USD"}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == 2 * 10**308
    assert out["cash_after"] == 2 * 10**308
    assert out["orders"] == []


def test_rebalance_preserves_extremely_small_actual_weight():
    raw = (
        '{"base_currency":"USD","cash":1e308,"positions":[{"symbol":"A","quantity":1,"price":1e-400,"currency":"USD"}],'
        '"targets":[{"symbol":"A","weight":1e-708,"lot_size":1}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["positions_after"][0]["value_base"] == Fraction("1e-400")
    assert 0 < out["positions_after"][0]["weight"] <= Fraction("1e-708")


def test_rebalance_preserves_tiny_fee_and_cash_amounts_exactly():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 2,
            "positions": [],
            "targets": [{"symbol": "A", "weight": 1, "price": 1, "currency": "USD", "lot_size": 1}],
            "fee_rate": 0.0000004,
        },
    )
    assert code == 0
    assert result["cash_after"] == pytest.approx(0.9999996)
    assert result["orders"][0]["estimated_cash_flow_base"] == pytest.approx(-1.0000004)
    assert result["estimated_cost_base"] == pytest.approx(0.0000004)


def test_rebalance_preserves_tiny_cash_balance_exactly():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 0.0000004,
            "positions": [],
            "targets": [{"symbol": "A", "weight": 0, "price": 1, "currency": "USD"}],
        },
    )
    assert code == 0
    assert result["initial_equity"] == pytest.approx(0.0000004)
    assert result["equity_after"] == pytest.approx(0.0000004)
    assert result["cash_after"] == pytest.approx(0.0000004)
    assert result["cash_weight_after"] == 1
    assert result["orders"] == []


def test_rebalance_preserves_very_large_fractional_cash_exactly():
    cash = str(10**1000 + 12345) + ".5"
    raw = f'{{"base_currency":"USD","cash":{cash},"positions":[],"targets":[{{"symbol":"A","weight":0,"price":1,"currency":"USD"}}]}}'
    code, out = invoke_raw(raw)
    expected = Fraction(10**1000 + 12345) + Fraction(1, 2)
    assert code == 0
    assert out["initial_equity"] == expected
    assert out["equity_after"] == expected
    assert out["cash_after"] == expected
    assert out["orders"] == []


def test_rebalance_full_exit_preserves_long_fractional_quantity_exactly():
    quantity = "0." + "1234567890" * 100 + "1"
    raw = f'{{"base_currency":"USD","cash":0,"positions":[{{"symbol":"A","quantity":{quantity},"price":1,"currency":"USD"}}],"targets":[{{"symbol":"A","weight":0}}]}}'
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["orders"][0]["side"] == "sell"
    assert out["orders"][0]["quantity"] == Fraction(quantity)
    assert out["orders"][0]["estimated_cash_flow_base"] == Fraction(quantity)
    assert out["positions_after"] == []


def test_rebalance_accepts_long_integer_and_extreme_exponents():
    for cash in ["2" + "0" * 4300, "1.5e1000000", "1.5e-1000000", "1e4300"]:
        raw = f'{{"base_currency":"USD","cash":{cash},"positions":[],"targets":[{{"symbol":"A","weight":0,"price":1,"currency":"USD"}}]}}'
        code, out = invoke_raw(raw, exact=False)
        assert code == 0, cash
        expected = format(Decimal(cash), "f")
        assert out["initial_equity"] == expected
        assert out["cash_after"] == expected
        assert out["orders"] == []


def test_rebalance_preserves_terminating_decimal_exactly():
    cash = "0.0000000000001136868377216160297393798828125"
    raw = f'{{"base_currency":"USD","cash":{cash},"positions":[],"targets":[{{"symbol":"A","weight":0,"price":1,"currency":"USD"}}]}}'
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == Fraction(Decimal(cash))
    assert out["equity_after"] == Fraction(Decimal(cash))
    assert out["cash_after"] == Fraction(Decimal(cash))


def test_rebalance_preserves_dyadic_long_fraction_exactly():
    cash = "0.0000000000000000000000000000007888609052210118054117285652827862296732064351090230047702789306640625"
    raw = f'{{"base_currency":"USD","cash":{cash},"positions":[],"targets":[{{"symbol":"A","weight":0,"price":1,"currency":"USD"}}]}}'
    code, out = invoke_raw(raw)
    assert code == 0
    assert out["initial_equity"] == Fraction(Decimal(cash))
    assert out["equity_after"] == Fraction(Decimal(cash))
    assert out["cash_after"] == Fraction(Decimal(cash))


def test_portfolio_stress_preserves_decimal_market_value_before_fraction():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "A", "quantity": 3, "price": 0.1, "currency": "USD"}],
            "stress_scenarios": [{"name": "unchanged"}],
        },
        exact=True,
    )
    assert code == 0
    scenario = result["stress_tests"][0]
    assert result["total_equity"] == scenario["equity_before"] == scenario["equity_after"] == Fraction("0.3")
    assert result["positions"][0]["value_base"] == Fraction("0.3")
    assert scenario["positions"][0]["value_after"] == Fraction("0.3")


def test_portfolio_stress_preserves_raw_decimal_price_exactly():
    price = "0.3000000000000000000000000000000000000001"
    raw = f'{{"base_currency":"USD","cash":0,"positions":[{{"symbol":"A","quantity":1,"price":{price},"currency":"USD"}}],"stress_scenarios":[{{"name":"unchanged"}}]}}'
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    scenario = result["stress_tests"][0]
    assert result["positions"][0]["value_base"] == Fraction(Decimal(price))
    assert scenario["equity_before"] == scenario["equity_after"] == Fraction(Decimal(price))
    assert scenario["positions"][0]["value_after"] == Fraction(Decimal(price))


def test_portfolio_stress_accepts_tiny_positive_raw_price():
    raw = '{"base_currency":"USD","cash":0,"positions":[{"symbol":"A","quantity":1,"price":1e-400,"currency":"USD"}],"stress_scenarios":[{"name":"unchanged"}]}'
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    scenario = result["stress_tests"][0]
    assert result["positions"][0]["value_base"] == Fraction(Decimal("1e-400"))
    assert scenario["equity_before"] == scenario["equity_after"] == Fraction(Decimal("1e-400"))
    assert result["positions"][0]["weight"] == 1


def test_portfolio_preserves_large_integer_equity_without_stress():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "A", "quantity": 9007199254740993, "price": 1, "currency": "USD"}],
        },
        exact=True,
    )
    assert code == 0
    assert result["total_equity"] == 9007199254740993
    assert result["positions"][0]["value_base"] == 9007199254740993


def test_portfolio_preserves_large_integer_equity_with_integral_float_price():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0.0,
            "positions": [{"symbol": "A", "quantity": 9007199254740993, "price": 1.0, "currency": "USD"}],
        },
        exact=True,
    )
    assert code == 0
    assert result["total_equity"] == 9007199254740993
    assert result["positions"][0]["value_base"] == 9007199254740993


def test_portfolio_stress_preserves_large_integer_pnl_fields():
    quantity = 9007199254740993
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [
                {"symbol": "A", "quantity": quantity, "price": 2, "cost": 1, "stop_price": 1, "currency": "USD"}
            ],
            "stress_scenarios": [{"name": "unchanged"}],
        },
        exact=True,
    )
    assert code == 0
    assert result["total_equity"] == 2 * quantity
    assert result["price_pnl_base"] == quantity
    assert result["stop_loss_risk_base"] == quantity
    assert result["stress_tests"][0]["equity_before"] == 2 * quantity
    assert result["stress_tests"][0]["equity_after"] == 2 * quantity


@pytest.mark.parametrize(
    "closes",
    [
        ["1.00000000000000001", "1.00000000000000002", "1.00000000000000001", "1.00000000000000003"],
        ["1e-400", "2e-400", "3e-400", "4e-400"],
        ["1e-400", "1e308", "1e-400", "1e308"],
        ["1e-400", "1e400", "1e-400", "1e400"],
        ["1", "1." + "0" * 399 + "1", "1", "1." + "0" * 399 + "1"],
    ],
)
def test_portfolio_stress_computes_correlation_from_exact_returns(closes):
    history_rows = ",".join(f'{{"date":"2026-01-0{i + 1}","close":{close}}}' for i, close in enumerate(closes))
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_rows}]}},{{"symbol":"B","prices":[{history_rows}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_correlation_centers_before_scaling():
    c0, c1 = 1, 10**20
    c2 = c1 * (c1 + 1)
    c3 = c2 * c1
    closes = [c0, c1, c2, c3]
    history_rows = ",".join(f'{{"date":"2026-01-0{i + 1}","close":{close}}}' for i, close in enumerate(closes))
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_rows}]}},{{"symbol":"B","prices":[{history_rows}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_correlation_preserves_close_return_variation():
    closes = [
        "1",
        "2.00000000000000000001",
        "4.0000000000000000000600000000000000000002",
        "8.000000000000000000240000000000000000002200000000000000000006",
    ]
    history_rows = ",".join(f'{{"date":"2026-01-0{i + 1}","close":{close}}}' for i, close in enumerate(closes))
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_rows}]}},{{"symbol":"B","prices":[{history_rows}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_large_history_correlation_is_fast_and_exact():
    rows = ",".join(
        f'{{"date":"{(date(2020, 1, 1) + timedelta(days=i)).isoformat()}","close":{1 + (i % 3)}}}' for i in range(5000)
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{rows}]}},{{"symbol":"B","prices":[{rows}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 4999


def test_portfolio_preserves_raw_decimal_without_stress():
    price = "0.3000000000000000000000000000000000000001"
    raw = f'{{"base_currency":"USD","cash":0,"positions":[{{"symbol":"A","quantity":1,"price":{price},"currency":"USD"}}]}}'
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["total_equity"] == Fraction(Decimal(price))
    assert result["positions"][0]["value_base"] == Fraction(Decimal(price))


def test_portfolio_preserves_large_decimal_price_without_stress():
    raw = '{"base_currency":"USD","cash":0,"positions":[{"symbol":"A","quantity":1,"price":9007199254740993.0,"currency":"USD"}]}'
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["total_equity"] == 9007199254740993
    assert result["positions"][0]["value_base"] == 9007199254740993


def test_portfolio_stress_many_symbols_large_history_is_fast_and_exact():
    symbols = [f"S{i}" for i in range(10)]
    rows = ",".join(
        f'{{"date":"{(date(2020, 1, 1) + timedelta(days=i)).isoformat()}","close":{1 + (i % 3)}}}' for i in range(20000)
    )
    positions = ",".join(f'{{"symbol":"{symbol}","quantity":1,"price":1,"currency":"USD"}}' for symbol in symbols)
    histories = ",".join(f'{{"symbol":"{symbol}","prices":[{rows}]}}' for symbol in symbols)
    raw = (
        f'{{"base_currency":"USD","cash":0,"positions":[{positions}],'
        f'"histories":[{histories}],"stress_scenarios":[{{"name":"unchanged"}}]}}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert len(result["correlations"]) == 45
    assert all(item["correlation"] == pytest.approx(1) for item in result["correlations"])
    assert result["correlations"][0]["return_samples"] == 19999


def test_portfolio_stress_all_pairs_large_history_is_fast_and_exact():
    symbols = [f"S{i}" for i in range(100)]
    rows = ",".join(
        f'{{"date":"{(date(2020, 1, 1) + timedelta(days=i)).isoformat()}","close":{1 + (i % 3)}}}' for i in range(5000)
    )
    positions = ",".join(f'{{"symbol":"{symbol}","quantity":1,"price":1,"currency":"USD"}}' for symbol in symbols)
    histories = ",".join(f'{{"symbol":"{symbol}","prices":[{rows}]}}' for symbol in symbols)
    raw = (
        f'{{"base_currency":"USD","cash":0,"positions":[{positions}],'
        f'"histories":[{histories}],"stress_scenarios":[{{"name":"unchanged"}}]}}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert len(result["correlations"]) == 4950
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 4999


def test_portfolio_stress_varied_calendar_correlation_is_exact():
    symbols = [f"S{i}" for i in range(20)]
    histories = []
    for index, symbol in enumerate(symbols):
        rows = ",".join(
            f'{{"date":"{(date(2020, 1, 1) + timedelta(days=i)).isoformat()}","close":{1 + (i % 3)}}}'
            for i in range(2000)
            if i != index
        )
        histories.append(f'{{"symbol":"{symbol}","prices":[{rows}]}}')
    positions = ",".join(f'{{"symbol":"{symbol}","quantity":1,"price":1,"currency":"USD"}}' for symbol in symbols)
    raw = (
        f'{{"base_currency":"USD","cash":0,"positions":[{positions}],'
        f'"histories":[{",".join(histories)}],"stress_scenarios":[{{"name":"unchanged"}}]}}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert len(result["correlations"]) == 190
    assert all(item["correlation"] == pytest.approx(1) for item in result["correlations"])


def test_portfolio_stress_maximum_varied_calendar_is_bounded():
    symbols = [f"S{i}" for i in range(100)]
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(20000)]
    payload = {
        "base_currency": "USD",
        "cash": 0,
        "positions": [{"symbol": symbol, "quantity": 1, "price": 1, "currency": "USD"} for symbol in symbols],
        "histories": [
            {
                "symbol": symbol,
                "prices": [
                    {"date": day, "close": 1 + (index % 3)} for index, day in enumerate(days) if index != omitted
                ],
            }
            for omitted, symbol in enumerate(symbols)
        ],
        "stress_scenarios": [{"name": "unchanged"}],
    }
    start = time.monotonic()
    code, result = invoke_raw(json.dumps(payload), command="portfolio", timeout=150)
    elapsed = time.monotonic() - start
    assert code == 0
    assert len(result["correlations"]) == 4950
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 19997
    assert elapsed < 150


def test_portfolio_correlation_is_clamped_to_unit_interval():
    closes = [207639, 451241, 195881, 221034]
    history_rows = ",".join(f'{{"date":"2026-01-0{i + 1}","close":{close}}}' for i, close in enumerate(closes))
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_rows}]}},{{"symbol":"B","prices":[{history_rows}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] <= 1
    assert result["correlations"][0]["correlation"] == pytest.approx(1)


def test_portfolio_stress_zero_daily_scale_does_not_hide_multiday_variation():
    histories = [
        {
            "symbol": "A",
            "prices": [
                {"date": day, "close": close}
                for day, close in zip(
                    ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-06"],
                    [1, 2, 4, 8, 16, 32],
                )
            ],
        },
        {
            "symbol": "B",
            "prices": [
                {"date": day, "close": close}
                for day, close in zip(
                    ["2026-01-01", "2026-01-02", "2026-01-04", "2026-01-05", "2026-01-06"],
                    [1, 3, 27, 81, 243],
                )
            ],
        },
    ]
    raw = json.dumps(
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [
                {"symbol": "A", "quantity": 1, "price": 1, "currency": "USD"},
                {"symbol": "B", "quantity": 1, "price": 1, "currency": "USD"},
            ],
            "histories": histories,
            "stress_scenarios": [{"name": "unchanged"}],
        }
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 4


def test_portfolio_stress_gap_scale_overflow_uses_common_vector():
    tiny = "0" * 399
    closes_a = ["1", "2", f"4.{tiny}1", f"8.{tiny}2", f"16.{tiny}4"]
    history_a = ",".join(f'{{"date":"2026-01-0{i + 1}","close":{close}}}' for i, close in enumerate(closes_a))
    history_b = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}' for day, close in [(1, 1), (2, 2), (4, 6), (5, 24)]
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_a}]}},{{"symbol":"B","prices":[{history_b}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(0, abs=1e-12)
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_extreme_spike_does_not_zero_common_vector():
    history_a = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}'
        for day, close in [(1, 1), (2, 2), (3, 3), (4, "4e400"), (5, 8), (6, 16)]
    )
    history_b = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}' for day, close in [(1, 1), (2, 2), (3, 3), (5, 8), (6, 16)]
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_a}]}},{{"symbol":"B","prices":[{history_b}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 4


def test_portfolio_stress_extreme_full_center_does_not_zero_common_vector():
    history_a = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}'
        for day, close in [(1, 1), (2, "1e400"), (3, "2e400"), (4, "6e400"), (5, "24e400")]
    )
    history_b = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}'
        for day, close in [(2, "1e400"), (3, "2e400"), (4, "6e400"), (5, "24e400")]
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_a}]}},{{"symbol":"B","prices":[{history_b}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1)
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_underflowed_subset_recomputes_common_vector():
    history_a = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}'
        for day, close in [(1, 1), (2, 1), (3, "1e38"), (4, "3e76"), (5, "1e276")]
    )
    history_b = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}'
        for day, close in [(1, 1), (2, 1), (3, "3e38"), (4, "3e76"), (6, "1e276")]
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_a}]}},{{"symbol":"B","prices":[{history_b}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(1 / 7)
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_constant_common_returns_remain_undefined():
    history_a = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}'
        for day, close in [(1, 1), (2, 1), (3, "1.1"), (4, "1.21"), (5, "1.331"), (6, "2.662")]
    )
    history_b = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}' for day, close in [(1, 1), (3, 1), (4, 2), (5, 6)]
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_a}]}},{{"symbol":"B","prices":[{history_b}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] is None
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_constant_huge_common_returns_remain_undefined():
    magnitude = 10**50

    def history(extra_days, multipliers):
        days = sorted([1, 4, 7, 10] + extra_days)
        prices = {days[0]: 1}
        price = 1
        for day, multiplier in zip(days[1:], multipliers):
            price *= 1 + multiplier * magnitude
            prices[day] = price
        return [{"date": f"2026-01-{day:02d}", "close": prices[day]} for day in days]

    payload = {
        "base_currency": "USD",
        "cash": 0,
        "positions": [
            {"symbol": "A", "quantity": 1, "price": 1, "currency": "USD"},
            {"symbol": "B", "quantity": 1, "price": 1, "currency": "USD"},
        ],
        "histories": [
            {"symbol": "A", "prices": history([2, 5, 8], [3, 1, 3, 8, 2, 0])},
            {"symbol": "B", "prices": history([3, 6, 9], [3, 4, 3, 4, 3, 4])},
        ],
        "stress_scenarios": [{"name": "unchanged"}],
    }
    code, result = invoke_raw(json.dumps(payload), command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] is None
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_constant_exponent_gap_returns_remain_undefined():
    history_a = ",".join(
        f'{{"date":"2026-01-{day:02d}","close":{close}}}' for day, close in [(1, 1), (4, 2), (7, 4), (10, 10)]
    )
    history_b = ",".join(
        f'{{"date":"2026-01-{day:02d}","close":{close}}}'
        for day, close in [(1, 1), (3, "1.6e12"), (4, "1e26"), (6, "1.6e38"), (7, "1e52"), (9, "1.6e64"), (10, "1e78")]
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_a}]}},{{"symbol":"B","prices":[{history_b}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] is None
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_catastrophic_cancellation_recomputes_common_vector():
    with localcontext() as context:
        context.prec = 140
        ratio = Decimal(10000000001)
        closes = [Decimal(1), ratio, ratio * (ratio + Decimal("0.000002"))]
        for _ in range(4):
            closes.append(ratio * closes[-1])
        history_a = ",".join(
            f'{{"date":"2026-01-0{day}","close":{format(close, "f")}}}' for day, close in enumerate(closes, start=1)
        )
    history_b = ",".join(
        f'{{"date":"2026-01-0{day}","close":{close}}}' for day, close in [(1, 1), (3, 7), (5, 8), (7, 9)]
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_a}]}},{{"symbol":"B","prices":[{history_b}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(0.999996524972417, rel=1e-12)
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_constant_integer_gap_returns_remain_undefined():
    def history(extra_offsets):
        offsets = sorted([0, 100, 200, 300] + extra_offsets)
        close = 1
        prices = []
        for index, offset in enumerate(offsets):
            if index:
                close *= 10**15 if index % 2 else 10**15 + 1
            prices.append({"date": (date(2020, 1, 1) + timedelta(days=offset)).isoformat(), "close": close})
        return prices

    payload = {
        "base_currency": "USD",
        "cash": 0,
        "positions": [
            {"symbol": "A", "quantity": 1, "price": 1, "currency": "USD"},
            {"symbol": "B", "quantity": 1, "price": 1, "currency": "USD"},
        ],
        "histories": [
            {"symbol": "A", "prices": history([1, 101, 201])},
            {"symbol": "B", "prices": history([2, 102, 202])},
        ],
        "stress_scenarios": [{"name": "unchanged"}],
    }
    code, result = invoke_raw(json.dumps(payload), command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] is None
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_stress_extreme_dynamic_range_uses_exact_common_scaling():
    history_a = ",".join(
        f'{{"date":"2026-02-{day:02d}","close":{close}}}'
        for day, close in [(1, 6000), (3, "8e30"), (6, "2e-17"), (7, "0.00001"), (10, 2000000000)]
    )
    history_b = ",".join(
        f'{{"date":"2026-02-{day:02d}","close":{close}}}'
        for day, close in [(3, "5e-20"), (6, "8e-25"), (7, "1.1e24"), (10, "1.6e-17")]
    )
    raw = (
        '{"base_currency":"USD","cash":0,"positions":['
        '{"symbol":"A","quantity":1,"price":1,"currency":"USD"},'
        '{"symbol":"B","quantity":1,"price":1,"currency":"USD"}],'
        f'"histories":[{{"symbol":"A","prices":[{history_a}]}},{{"symbol":"B","prices":[{history_b}]}}],'
        '"stress_scenarios":[{"name":"unchanged"}]}'
    )
    code, result = invoke_raw(raw, command="portfolio")
    assert code == 0
    assert result["correlations"][0]["correlation"] == pytest.approx(-0.498121482921633, rel=1e-12)
    assert result["correlations"][0]["return_samples"] == 3


def test_portfolio_zero_shock_preserves_equity_and_pnl_order_independence():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 1.0,
            "positions": [
                {"symbol": "A", "quantity": 1.0, "price": 0.1, "currency": "USD"},
                {"symbol": "C", "quantity": 1.0, "price": 10000000000000000.0, "currency": "USD"},
            ],
            "stress_scenarios": [{"name": "unchanged"}],
        },
        exact=True,
    )
    assert code == 0
    scenario = result["stress_tests"][0]
    assert (
        result["total_equity"]
        == scenario["equity_before"]
        == scenario["equity_after"]
        == Fraction("10000000000000001.1")
    )
    assert scenario["pnl_base"] == 0
    assert all(position["pnl_base"] == 0 for position in scenario["positions"])


@pytest.mark.parametrize("lot_size", ["1.5", "-1", "1e-1000000"])
def test_rebalance_validates_supplied_zero_weight_lot_size(lot_size):
    raw = f'{{"base_currency":"USD","cash":0,"positions":[],"targets":[{{"symbol":"A","weight":0,"price":1,"currency":"USD","lot_size":{lot_size}}}]}}'
    code, out = invoke_raw(raw)
    assert code == 1
    assert "error" in out


def test_rebalance_handles_large_nonterminating_lot_residual():
    raw = (
        '{"base_currency":"USD","cash":1e1000002,"positions":[],"targets":['
        '{"symbol":"A","weight":1,"price":3,"currency":"USD","lot_size":1e1000001}]}'
    )
    code, out = invoke_raw(raw, exact=False)
    assert code == 0
    assert out["orders"][0]["quantity"] == "3" + "0" * 1000001
    assert out["unfilled"][0]["reason"] == "lot_rounding"
    residual = Decimal(out["unfilled"][0]["quantity"])
    assert 0 < residual < Decimal("1e1000001")


def test_portfolio_small_shock_is_not_lost_in_large_total():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [
                {"symbol": "SMALL", "quantity": 1, "price": 0.1, "currency": "USD"},
                {"symbol": "LARGE", "quantity": 1, "price": 10000000000000000, "currency": "USD"},
            ],
            "stress_scenarios": [{"name": "small_up", "symbol_shocks": [{"symbol": "SMALL", "shock": 1}]}],
        },
        exact=True,
    )
    assert code == 0
    scenario = result["stress_tests"][0]
    assert scenario["equity_after"] - scenario["equity_before"] == scenario["pnl_base"]
    assert scenario["equity_after"] == Fraction("10000000000000000.2")
    assert scenario["pnl_base"] == pytest.approx(0.1)
    assert scenario["return_pct"] == pytest.approx(1e-15)
    assert scenario["positions"][0]["pnl_base"] == pytest.approx(0.1)


def test_portfolio_tiny_shock_keeps_large_position_pnl():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "LARGE", "quantity": 1, "price": 10000000000000000, "currency": "USD"}],
            "stress_scenarios": [{"name": "tiny_up", "market_shock": 1e-16}],
        },
        exact=True,
    )
    assert code == 0
    scenario = result["stress_tests"][0]
    assert scenario["equity_before"] == 10000000000000000
    assert scenario["equity_after"] == 10000000000000001
    assert scenario["positions"][0]["value_after"] == 10000000000000001
    assert scenario["positions"][0]["pnl_base"] == pytest.approx(1)
    assert scenario["pnl_base"] == pytest.approx(1)
    assert scenario["return_pct"] == pytest.approx(1e-14)


def test_portfolio_extreme_scale_stress_reconciles_exactly():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [
                {"symbol": "LARGE", "quantity": 1, "price": 1e100, "currency": "USD"},
                {"symbol": "SMALL", "quantity": 1, "price": 1e-100, "currency": "USD"},
            ],
            "stress_scenarios": [{"name": "small_up", "symbol_shocks": [{"symbol": "SMALL", "shock": 1}]}],
        },
        exact=True,
    )
    assert code == 0
    scenario = result["stress_tests"][0]
    expected_before = Fraction(Decimal("1e100")) + Fraction(Decimal("1e-100"))
    expected_after = Fraction(Decimal("1e100")) + 2 * Fraction(Decimal("1e-100"))
    assert result["total_equity"] == scenario["equity_before"] == expected_before
    assert scenario["equity_after"] == expected_after
    assert scenario["equity_after"] - scenario["equity_before"] == scenario["pnl_base"]
    assert scenario["positions"][1]["pnl_base"] == Fraction(Decimal("1e-100"))


def test_portfolio_large_value_tiny_shock_preserves_after_value():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "LARGE", "quantity": 1, "price": 1e100, "currency": "USD"}],
            "stress_scenarios": [{"name": "tiny", "market_shock": 1e-100}],
        },
        exact=True,
    )
    assert code == 0
    scenario = result["stress_tests"][0]
    assert scenario["pnl_base"] == 1
    assert scenario["equity_after"] == Fraction(Decimal("1e100")) + 1
    assert scenario["positions"][0]["value_after"] == Fraction(Decimal("1e100")) + 1


def test_portfolio_zero_shock_equity_matches_headline_with_many_small_positions():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "LARGE", "quantity": 1, "price": 10000000000000000, "currency": "USD"}]
            + [{"symbol": f"SMALL{i}", "quantity": 1, "price": 0.1, "currency": "USD"} for i in range(10)],
            "stress_scenarios": [{"name": "unchanged"}],
        },
        exact=True,
    )
    assert code == 0
    scenario = result["stress_tests"][0]
    assert result["total_equity"] == scenario["equity_before"] == scenario["equity_after"] == 10000000000000001
    assert scenario["pnl_base"] == 0


def test_rebalance_rounds_nonterminating_lot_residual_toward_zero():
    raw = (
        '{"base_currency":"USD","cash":5.999999999999999999999999999999999999999999999999999999999999,'
        '"positions":[],"targets":[{"symbol":"A","weight":1,"price":3,"currency":"USD","lot_size":1}]}'
    )
    code, out = invoke_raw(raw)
    assert code == 0
    assert len(out["orders"]) == 1
    assert out["unfilled"][0]["reason"] == "lot_rounding"
    assert 0 < out["unfilled"][0]["quantity"] < 1


def test_rebalance_rejects_turnover_above_explicit_limit():
    code, result = invoke(
        "rebalance",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "AAA", "quantity": 10, "price": 10, "currency": "USD"}],
            "targets": [
                {"symbol": "AAA", "weight": 0},
                {"symbol": "BBB", "weight": 1, "price": 10, "currency": "USD", "lot_size": 1},
            ],
            "max_turnover_fraction": 0.5,
        },
    )
    assert code == 1
    assert result["error_code"] == "turnover_limit_exceeded"
    assert result["required_turnover_fraction"] == 2


def test_portfolio_stress_rejects_unknown_symbol_instead_of_ignoring_it():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 100,
            "positions": [{"symbol": "AAA", "quantity": 1, "price": 10, "currency": "USD"}],
            "stress_scenarios": [{"name": "typo", "symbol_shocks": [{"symbol": "AA", "shock": -0.1}]}],
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


def test_portfolio_supports_large_finite_values():
    code, result = invoke(
        "portfolio",
        {
            "base_currency": "USD",
            "cash": 0,
            "positions": [{"symbol": "AAPL", "quantity": 1e200, "price": 1e200, "currency": "USD"}],
        },
        exact=True,
    )
    assert code == 0
    assert result["total_equity"] == Fraction(Decimal("1e400"))
    assert result["positions"][0]["value_base"] == Fraction(Decimal("1e400"))


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


def test_event_without_valid_window_does_not_mark_neighbor_as_overlapping():
    dates = [f"2026-01-{day:02d}" for day in range(1, 13)]
    prices = [{"date": day, "close": 100} for day in dates]
    code, result = invoke(
        "event_study",
        {
            "event_dates": ["2026-01-01", "2026-01-02"],
            "horizons": [0, 5],
            "price_basis": "adjusted",
            "prices": prices,
            "benchmark_prices": prices,
        },
    )
    assert code == 0
    assert all(window["reason"] for window in result["events"][0]["windows"])
    assert not any(event["overlaps_other_event"] for event in result["events"])


def test_tail_event_with_only_invalid_horizons_does_not_overlap():
    dates = [f"2026-01-{day:02d}" for day in range(1, 13)]
    prices = [{"date": day, "close": 100} for day in dates]
    code, result = invoke(
        "event_study",
        {
            "event_dates": ["2026-01-05", "2026-01-10"],
            "horizons": [5],
            "price_basis": "adjusted",
            "prices": prices,
            "benchmark_prices": prices,
        },
    )
    assert code == 0
    assert result["events"][1]["windows"][0]["reason"] == "insufficient_stock_window"
    assert not any(event["overlaps_other_event"] for event in result["events"])


def test_event_without_excess_return_sample_does_not_overlap():
    dates = [f"2026-01-{day:02d}" for day in range(1, 13)]
    prices = [{"date": day, "close": 100} for day in dates]
    benchmark = [row for row in prices if row["date"] != "2026-01-03"]
    code, result = invoke(
        "event_study",
        {
            "event_dates": ["2026-01-03", "2026-01-04"],
            "horizons": [2],
            "price_basis": "adjusted",
            "prices": prices,
            "benchmark_prices": benchmark,
        },
    )
    assert code == 0
    assert result["events"][1]["windows"][0]["reason"] == "missing_benchmark_endpoint"
    assert not any(event["overlaps_other_event"] for event in result["events"])


def test_invalid_horizon_does_not_expand_overlap_interval():
    dates = [f"2026-01-{day:02d}" for day in range(1, 9)]
    prices = [{"date": day, "close": 100} for day in dates]
    benchmark = [row for row in prices if row["date"] != "2026-01-05"]
    code, result = invoke(
        "event_study",
        {
            "event_dates": ["2026-01-03", "2026-01-04"],
            "horizons": [0, 2],
            "price_basis": "adjusted",
            "prices": prices,
            "benchmark_prices": benchmark,
        },
    )
    assert code == 0
    assert result["events"][0]["windows"][0]["excess_return_pct"] == 0
    assert result["events"][0]["windows"][1]["reason"] == "missing_benchmark_endpoint"
    assert not any(event["overlaps_other_event"] for event in result["events"])


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
