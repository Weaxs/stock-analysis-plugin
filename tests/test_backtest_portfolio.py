"""Tests for multi-asset portfolio, DCA, and periodic rebalance backtesting."""

import numpy as np
import pandas as pd
import pytest

from tools import backtest


def _make_df(dates, prices, volume=1_000_000, opens=None, limit_up=None, limit_down=None, currency=None):
    data = {
        "date": pd.to_datetime(dates),
        "open": opens if opens is not None else prices,
        "high": [p * 1.02 for p in prices] if limit_up is None and limit_down is None else prices,
        "low": [p * 0.98 for p in prices] if limit_up is None and limit_down is None else prices,
        "close": prices,
        "volume": volume if isinstance(volume, list) else [volume] * len(dates),
        "adjusted": ["qfq"] * len(dates),
    }
    if limit_up is not None:
        data["limit_up"] = limit_up
    if limit_down is not None:
        data["limit_down"] = limit_down
    if currency is not None:
        data["currency"] = [currency] * len(dates)
    return pd.DataFrame(data)


def test_monthly_rebalance_sells_winner_and_buys_laggard():
    dates = pd.bdate_range("2025-01-02", periods=65)
    a_prices = np.linspace(10.0, 20.0, 65)
    b_prices = np.full(65, 10.0)
    datasets = {"510300": _make_df(dates, a_prices), "510500": _make_df(dates, b_prices)}
    strategy = {
        "name": "Equal Rebalance",
        "portfolio": {"rebalance": "monthly", "weighting": "equal"},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio(datasets, strategy, capital=100_000, start="2025-01-03")
    assert res["mode"] == "portfolio_allocation"
    assert res["final_equity"] > 100_000
    sells = [t for t in res["trades"] if t["type"] == "sell"]
    assert any(t["symbol"] == "510300" and "再平衡" in t["reason"] for t in sells)
    assert len(res["final_holdings"]) == 2
    weights = {h["symbol"]: h["weight"] for h in res["final_holdings"]}
    assert 0.4 <= weights["510300"] <= 0.6


def test_flat_price_dca_has_zero_drawdown_zero_sharpe_and_zero_twr(tmp_path, monkeypatch):
    dates = pd.bdate_range("2025-01-02", periods=65)
    df = _make_df(dates, np.full(65, 10.0))
    monkeypatch.setattr(backtest, "fetch_kline", lambda sym, *a, **kw: df)
    strat = tmp_path / "flat_dca.yaml"
    strat.write_text(
        "name: FlatDCA\ndca:\n  frequency: monthly\n  amount: 10000\nsimulation:\n  execution: next_open\n  slippage: 0\n  commission: 0\n  stamp_tax: 0\n",
        encoding="utf-8",
    )
    res = backtest.run_backtest(str(strat), "AAPL", "2025-01-02", "2025-04-02", 10_000)
    assert res["contributions"]["count"] >= 2
    assert res["metrics"]["time_weighted_return"] == 0.0
    assert res["metrics"]["max_drawdown"] == 0.0
    assert res["metrics"]["sharpe_ratio"] == 0.0
    assert res["metrics"]["annual_return"] == 0.0


def test_twr_links_overnight_jump_before_open_contribution():
    # Close day0=128, day1 opens at 256 (100% overnight gain), $128 DCA deposited at open -> TWR must be 100%, not 50%
    dates = pd.to_datetime(["2025-01-31", "2025-02-03", "2025-02-04"])
    df = pd.DataFrame(
        {
            "date": dates,
            "open": [128.0, 256.0, 256.0],
            "high": [128.0, 256.0, 256.0],
            "low": [128.0, 256.0, 256.0],
            "close": [128.0, 256.0, 256.0],
            "volume": [1000, 1000, 1000],
            "adjusted": ["qfq"] * 3,
        }
    )
    strategy = {
        "dca": {"frequency": "monthly", "amount": 128},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio({"AAPL": df}, strategy, capital=128, start="2025-01-31")
    assert res["time_weighted_return"] == 1.0


def test_biweekly_schedule_is_continuous_across_year_boundary():
    b1 = backtest._schedule_bucket(pd.Timestamp("2025-12-22"), "biweekly")
    b2 = backtest._schedule_bucket(pd.Timestamp("2025-12-29"), "biweekly")
    b3 = backtest._schedule_bucket(pd.Timestamp("2026-01-05"), "biweekly")
    assert b1 == b2
    assert b3 == b2 + 1


def test_dca_deploys_when_drift_threshold_skips_rebalance():
    dates = pd.bdate_range("2025-01-02", periods=65)
    df = _make_df(dates, np.full(65, 10.0))
    strategy = {
        "portfolio": {"rebalance": "monthly", "weighting": "equal", "drift_threshold": 0.2},
        "dca": {"frequency": "monthly", "amount": 100},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio({"AAPL": df}, strategy, capital=1_000, start="2025-01-03")
    assert res["contributions"]["count"] >= 2
    assert res["final_cash"] == 0.0
    assert any("定期定投" in t["reason"] for t in res["trades"])


def test_stop_loss_does_not_reenter_on_same_bar_and_persists_when_blocked():
    # Stop triggers on day 1 close (10 -> 7), day 2 has 0 volume (blocked), day 2 close recovers to 10, day 3 trades -> must still sell and not reenter same bar
    dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"])
    df = pd.DataFrame(
        {
            "date": dates,
            "open": [10.0, 10.0, 7.0, 10.0, 10.0],
            "high": [10.0, 10.0, 10.0, 10.0, 10.0],
            "low": [10.0, 7.0, 7.0, 10.0, 10.0],
            "close": [10.0, 7.0, 10.0, 10.0, 10.0],
            "volume": [1000, 1000, 0, 1000, 1000],
            "adjusted": ["qfq"] * 5,
        }
    )
    strategy = {
        "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 5}], "logic": "all"},
        "exit": {"conditions": [], "stop_loss": -0.05, "take_profit": 0.5},
        "position": {"max_positions": 1, "size": 0.5},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio({"AAPL": df}, strategy, capital=10_000, start="2025-01-03")
    # Day 1 buys using 50% size (5000 = 500 shares), day 2 stop blocked, day 3 sells and does NOT re-buy on day 3!
    buys_d3 = [t for t in res["trades"] if t["type"] == "buy" and t["date"] == "2025-01-07"]
    sells_d3 = [t for t in res["trades"] if t["type"] == "sell" and t["date"] == "2025-01-07"]
    assert len(sells_d3) == 1
    assert buys_d3 == []
    assert res["trades"][0]["shares"] == 500  # position.size=0.5 applied
    assert res["execution"]["blocked_orders"] == 1
    assert res["execution"]["unfilled_order"] is False


def test_staggered_start_date_retries_initial_allocation():
    d_a = pd.bdate_range("2025-01-02", periods=10)
    d_b = pd.bdate_range("2025-01-06", periods=8)
    datasets = {"AAPL": _make_df(d_a, np.full(10, 10.0)), "MSFT": _make_df(d_b, np.full(8, 10.0))}
    strategy = {
        "portfolio": {"rebalance": "none", "weighting": "equal"},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio(datasets, strategy, capital=10_000, start="2025-01-03")
    held = {h["symbol"]: h["shares"] for h in res["final_holdings"]}
    assert held == {"AAPL": 500, "MSFT": 500}
    assert res["final_cash"] == 0.0


def test_numeric_yaml_weight_keys_and_signal_rebalance_conflict_and_defaults():
    dates = pd.bdate_range("2025-01-02", periods=10)
    datasets = {"510300": _make_df(dates, np.full(10, 10.0)), "510500": _make_df(dates, np.full(10, 10.0))}
    # Numeric int key 510300 resolved cleanly
    strategy = {
        "portfolio": {"rebalance": "none", "weighting": "custom", "weights": {510300: 0.6, "510500": 0.4}},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio(datasets, strategy, capital=100_000, start="2025-01-03")
    weights = {h["symbol"]: h["weight"] for h in res["final_holdings"]}
    assert weights["510300"] == 0.6 and weights["510500"] == 0.4

    # Rebalance + entry.conditions conflict rejected
    with pytest.raises(ValueError, match="rebalance"):
        backtest.simulate_portfolio(
            datasets,
            {
                "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 1}]},
                "portfolio": {"rebalance": "monthly"},
            },
            capital=100_000,
        )

    # Single-symbol signal + DCA preserves default -10% stop_loss
    drop_df = _make_df(dates, [10, 10, 8, 8, 8, 8, 8, 8, 8, 8])
    sig_dca = {
        "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 9}]},
        "dca": {"frequency": "monthly", "amount": 1000},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res_stop = backtest.simulate_portfolio({"AAPL": drop_df}, sig_dca, capital=10_000, start="2025-01-03")
    assert any(t["type"] == "sell" and "止损" in t["reason"] for t in res_stop["trades"])


def test_price_limit_up_blocks_portfolio_buy():
    dates = pd.bdate_range("2025-01-02", periods=5)
    df = _make_df(dates, [10.0, 11.0, 11.0, 11.0, 11.0], limit_up=[11.0, 11.0, 12.1, 12.1, 12.1])
    strategy = {
        "portfolio": {"rebalance": "none", "weighting": "equal"},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio({"600519": df}, strategy, capital=100_000, start="2025-01-03")
    assert res["execution"]["blocked_orders"] == 1
    assert res["trades"][0]["date"] == "2025-01-06"


def test_duplicate_canonical_symbols_and_mixed_hk_counters_rejected():
    dates = pd.bdate_range("2025-01-02", periods=40)
    with pytest.raises(ValueError, match="Duplicate instrument"):
        backtest.simulate_portfolio(
            {"600519": _make_df(dates, np.full(40, 100.0)), "sh600519": _make_df(dates, np.full(40, 100.0))},
            {"portfolio": {"rebalance": "monthly"}},
            capital=100_000,
        )
    with pytest.raises(ValueError, match="quote currency"):
        backtest.simulate_portfolio(
            {"2807.HK": _make_df(dates, np.full(40, 10.0)), "9807.HK": _make_df(dates, np.full(40, 10.0))},
            {"portfolio": {"rebalance": "monthly"}},
            capital=100_000,
        )
    with pytest.raises(ValueError, match="single market"):
        backtest.simulate_portfolio(
            {"600519": _make_df(dates, np.full(40, 100.0)), "AAPL": _make_df(dates, np.full(40, 100.0))},
            {"portfolio": {"rebalance": "monthly"}},
            capital=100_000,
        )
