"""Tests for multi-asset portfolio, DCA, and periodic rebalance backtesting."""

import numpy as np
import pandas as pd
import pytest

from tools import backtest


def _make_df(dates, prices, volume=1_000_000):
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": prices,
            "high": [p * 1.02 for p in prices],
            "low": [p * 0.98 for p in prices],
            "close": prices,
            "volume": [volume] * len(dates),
            "adjusted": ["qfq"] * len(dates),
        }
    )


def test_monthly_rebalance_sells_winner_and_buys_laggard():
    dates = pd.bdate_range("2025-01-02", periods=65)
    # Asset A doubles then stays flat; Asset B stays flat -> rebalance trims A and adds B
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


def test_monthly_dca_tracks_contributions_and_twr():
    dates = pd.bdate_range("2025-01-02", periods=70)
    prices = np.linspace(10.0, 12.0, 70)
    datasets = {"AAPL": _make_df(dates, prices)}
    strategy = {
        "name": "Monthly DCA",
        "dca": {"frequency": "monthly", "amount": 5000},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio(datasets, strategy, capital=10_000, start="2025-01-03")
    assert res["contributions"]["count"] >= 2
    assert res["total_contributed"] == 10_000 + res["contributions"]["count"] * 5000
    assert res["final_equity"] > res["total_contributed"]
    assert res["time_weighted_return"] > 0.15


def test_multi_position_signal_mode_holds_multiple_symbols():
    dates = pd.bdate_range("2025-01-02", periods=50)
    prices_a = np.linspace(10.0, 15.0, 50)
    prices_b = np.linspace(20.0, 26.0, 50)
    datasets = {"AAPL": _make_df(dates, prices_a), "MSFT": _make_df(dates, prices_b)}
    strategy = {
        "name": "Multi Signal",
        "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 5}], "logic": "all"},
        "exit": {"conditions": [], "stop_loss": -0.2, "take_profit": 0.8},
        "position": {"max_positions": 2},
        "simulation": {"execution": "next_open", "slippage": 0.0, "commission": 0.0, "stamp_tax": 0.0},
    }
    res = backtest.simulate_portfolio(datasets, strategy, capital=100_000, start="2025-01-03")
    assert res["mode"] == "portfolio_signal"
    held = {h["symbol"] for h in res["final_holdings"]}
    assert held == {"AAPL", "MSFT"}


def test_mixed_currency_markets_rejected():
    dates = pd.bdate_range("2025-01-02", periods=40)
    datasets = {"600519": _make_df(dates, np.full(40, 100.0)), "AAPL": _make_df(dates, np.full(40, 100.0))}
    with pytest.raises(ValueError, match="single market"):
        backtest.simulate_portfolio(datasets, {"portfolio": {"rebalance": "monthly"}}, capital=100_000)


def test_run_backtest_dispatches_comma_separated_symbols(tmp_path, monkeypatch):
    dates = pd.bdate_range("2025-01-02", periods=60)
    dfs = {
        "510300": _make_df(dates, np.linspace(3.0, 3.6, 60)),
        "510500": _make_df(dates, np.linspace(5.0, 5.2, 60)),
    }
    monkeypatch.setattr(backtest, "fetch_kline", lambda sym, *a, **kw: dfs[sym])
    strat_path = tmp_path / "port.yaml"
    strat_path.write_text(
        "name: TestPort\nportfolio:\n  rebalance: monthly\n  weighting: equal\ndca:\n  frequency: monthly\n  amount: 2000\n",
        encoding="utf-8",
    )
    res = backtest.run_backtest(str(strat_path), "510300,510500", "2025-01-02", "2025-03-27", 50_000)
    assert res["symbols"] == ["510300", "510500"]
    assert res["total_contributed"] > 50_000
    assert "time_weighted_return" in res["metrics"]
    assert res["final_holdings"]
