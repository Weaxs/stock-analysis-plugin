import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from tools import backtest
from tools._subproc import json_safe as _json_safe
from tools.backtest import (
    SIGNAL_DEFINITIONS,
    _substitute,
    check_condition,
    compute_ema,
    compute_ema_diff,
    compute_ma,
    compute_ma_diff,
    compute_macd_dea,
    compute_macd_dif,
    compute_metrics,
    compute_rsi,
    diagnose,
    evaluate_conditions,
    evaluate_signal,
    sample_curve,
)


def test_trend_score_uses_true_range_and_masks_warmup(make_kline_data):
    bars = make_kline_data(4)
    for row, close in zip(bars, [100, 102, 104, 106], strict=True):
        row.update(open=close, close=close, high=close + 1, low=close - 1)
    score = backtest.get_indicator(pd.DataFrame(bars), "trend_score", period=2, fast=2, slow=3)
    assert score.iloc[:2].isna().all()
    # Worked example: final EMA2=105.037037..., EMA3=104.25, true-range mean=3.
    assert score.iloc[-1] == pytest.approx(0.2623456790123457)


def test_trend_score_is_causal_and_analysis_uses_identical_formula(make_kline_data):
    bars = make_kline_data(150)
    df = pd.DataFrame(bars)
    full = backtest.get_indicator(df, "trend_score")
    prefix = backtest.get_indicator(df.iloc[:120], "trend_score")
    pd.testing.assert_series_equal(full.iloc[:120], prefix)
    from tools.technical import analyze_records

    analysis = analyze_records("TEST", bars[:120])["trend_temperature"]
    assert analysis["score"] == prefix.iloc[-1]
    assert analysis["previous_score"] == prefix.iloc[-2]


@pytest.mark.parametrize(("period", "fast", "slow"), [(0, 2, 3), (2, 3, 3), (2, 4, 3), (2, True, 3), (2.5, 2, 3)])
def test_trend_score_rejects_invalid_periods(make_kline_data, period, fast, slow):
    with pytest.raises(ValueError, match="positive integer periods"):
        backtest.get_indicator(pd.DataFrame(make_kline_data(120)), "trend_score", period, fast, slow)


@pytest.mark.parametrize("parameter", ["period", "fast_period", "slow_period"])
@pytest.mark.parametrize("invalid", ["2.5", "invalid"])
def test_trend_score_conditions_reject_invalid_period_strings(make_kline_data, parameter, invalid):
    condition = {"indicator": "trend_score", "operator": ">", "value": 0, parameter: invalid}
    with pytest.raises(ValueError):
        evaluate_conditions(pd.DataFrame(make_kline_data(120)), [condition], "all", 119, {})


def test_trend_score_executes_only_after_close_signal(make_kline_data):
    bars = make_kline_data(8)
    for row, close in zip(bars, [100, 102, 104, 106, 108, 110, 112, 114], strict=True):
        row.update(open=close, close=close, high=close + 1, low=close - 1)
    strategy = {
        "entry": {
            "conditions": [
                {
                    "indicator": "trend_score",
                    "fast_period": 2,
                    "slow_period": 3,
                    "period": 2,
                    "operator": "cross_above",
                    "value": 0.25,
                }
            ]
        },
        "exit": {"take_profit": 0.5},
        "simulation": {"slippage": 0, "commission": 0},
    }
    trades = backtest.simulate(pd.DataFrame(bars), strategy, 10000, "AAPL")["trades"]
    assert len(trades) == 1
    assert trades[0]["signal_date"] == "2024-01-04"
    assert trades[0]["date"] == "2024-01-05"
    assert trades[0]["price"] == 108


def test_monthly_momentum_uses_first_visible_close_and_holds_until_next_month(make_kline_data):
    bars = make_kline_data(80)
    for i, row in enumerate(bars):
        row.update(open=100 + i, close=100 + i, high=101 + i, low=99 + i)
    df = pd.DataFrame(bars)
    series = backtest.get_indicator(df, "monthly_momentum", period=5)
    assert series.iloc[:23].isna().all()
    # Feb 1 is bar 23: close 123 versus close 118 five observations earlier.
    assert series.iloc[23] == pytest.approx(0.04149973090675284)
    assert series.iloc[24] == series.iloc[23]
    pd.testing.assert_series_equal(series.iloc[:45], backtest.get_indicator(df.iloc[:45], "monthly_momentum", period=5))


def test_monthly_momentum_fill_follows_observation_close(make_kline_data):
    bars = make_kline_data(80)
    for i, row in enumerate(bars):
        row.update(open=100 + i, close=100 + i, high=101 + i, low=99 + i)
    strategy = {
        "entry": {"conditions": [{"indicator": "monthly_momentum", "period": 5, "operator": ">", "value": 0}]},
        "exit": {"take_profit": 1e9},
        "simulation": {"slippage": 0, "commission": 0},
    }
    trades = backtest.simulate(pd.DataFrame(bars), strategy, 10000, "AAPL")["trades"]
    assert len(trades) == 1
    assert trades[0]["signal_date"] == "2024-02-01"
    assert trades[0]["date"] == "2024-02-02"
    assert trades[0]["price"] == 124


def test_monthly_momentum_does_not_observe_or_count_zero_volume_padding(make_kline_data):
    bars = make_kline_data(80)
    for i, row in enumerate(bars):
        row.update(open=100 + i, close=100 + i, high=101 + i, low=99 + i)
    bars[23]["volume"] = 0
    series = backtest.get_indicator(pd.DataFrame(bars), "monthly_momentum", period=5)
    assert pd.isna(series.iloc[23])
    assert series.iloc[24] == pytest.approx(0.04959694113937212)


@pytest.mark.parametrize("invalid", [0, True, 2.5, "invalid", "2.5"])
def test_monthly_momentum_conditions_reject_invalid_period(make_kline_data, invalid):
    condition = {"indicator": "monthly_momentum", "period": invalid, "operator": ">", "value": 0}
    with pytest.raises(ValueError):
        evaluate_conditions(pd.DataFrame(make_kline_data(120)), [condition], "all", 119, {})


def test_monthly_market_entry_waits_for_observation_and_does_not_force_exit(make_kline_data):
    bars = make_kline_data(360)
    market = make_kline_data(360)
    for i, (row, context) in enumerate(zip(bars, market, strict=True)):
        row.update(open=100 + i, close=100 + i, high=101 + i, low=99 + i)
        price = (
            500 if row["date"] < pd.Timestamp("2025-03-10") else 1000 if row["date"] < pd.Timestamp("2025-04-10") else 1
        )
        context.update(open=price, close=price, high=price + 0.1, low=price - 0.1)
    result = backtest.simulate_monthly_market(
        pd.DataFrame(bars).assign(date=lambda d: pd.to_datetime(d.date)),
        pd.DataFrame(market).assign(date=lambda d: pd.to_datetime(d.date)),
        100000,
        "AAPL",
        "2025-03-03",
        allocation=0.8704,
    )
    assert len(result["trades"]) == 3
    assert {trade["date"] for trade in result["trades"]} == {"2025-04-02"}
    assert all(trade["type"] == "buy" and trade["signal_date"] == "2025-04-01" for trade in result["trades"])
    assert all(row["equity"] == 100000 for row in result["equity_curve"] if row["date"] < "2025-04-02")
    assert result["validation_status"] == "not_validated"


def test_simulate_entry_gate_is_aligned_and_rejects_truthy_strings(make_kline_data):
    df = pd.DataFrame(make_kline_data(35))
    strategy = {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]}}
    with pytest.raises(ValueError, match="entry_gate"):
        backtest.simulate(df, strategy, 10000, "AAPL", entry_gate=pd.Series(["false"] * len(df)))
    with pytest.raises(ValueError, match="entry_gate"):
        backtest.simulate(df, strategy, 10000, "AAPL", entry_gate=pd.Series([True] * (len(df) - 1)))


def test_monthly_market_does_not_inherit_phantom_warmup_positions(make_kline_data):
    bars, market = make_kline_data(360), make_kline_data(360)
    for i, (row, context) in enumerate(zip(bars, market, strict=True)):
        row.update(open=100 + i, close=100 + i, high=101 + i, low=99 + i)
        price = 100 + i if row["date"] < pd.Timestamp("2025-02-01") else 1
        context.update(open=price, close=price, high=price + 0.1, low=price - 0.1)
    result = backtest.simulate_monthly_market(pd.DataFrame(bars), pd.DataFrame(market), 100000, "AAPL", "2025-03-03")
    assert result["trades"] == []
    assert result["final_equity"] == 100000


def test_monthly_market_cancels_unfilled_entry_when_next_month_is_weak(make_kline_data):
    bars, market = make_kline_data(360), make_kline_data(360)
    for i, (row, context) in enumerate(zip(bars, market, strict=True)):
        price = 100 + i
        row.update(open=price, close=price, high=price + 1, low=price - 1)
        if pd.Timestamp("2025-03-03") <= row["date"] <= pd.Timestamp("2025-04-01"):
            row.update(high=price, low=price, limit_up=price)
        market_price = price if row["date"] < pd.Timestamp("2025-03-10") else 1
        context.update(open=market_price, close=market_price, high=market_price + 0.1, low=market_price - 0.1)
    result = backtest.simulate_monthly_market(pd.DataFrame(bars), pd.DataFrame(market), 1000000, "600519", "2025-03-03")
    assert result["trades"] == []
    assert result["final_equity"] == 1000000


class TestComputeRsi:
    def test_uptrend_high_rsi(self):
        close = pd.Series([10 + i * 0.5 for i in range(50)])
        rsi = compute_rsi(close, 14)
        assert float(rsi.iloc[-1]) == 100

    def test_downtrend_low_rsi(self):
        close = pd.Series([30 - i * 0.5 for i in range(30)])
        rsi = compute_rsi(close, 14)
        assert float(rsi.iloc[-1]) == 0


class TestComputeMa:
    def test_ma_value(self):
        close = pd.Series([10.0] * 20)
        ma = compute_ma(close, 5)
        assert abs(float(ma.iloc[-1]) - 10.0) < 0.01

    def test_ma_ascending(self):
        close = pd.Series([float(i) for i in range(1, 21)])
        ma = compute_ma(close, 5)
        assert float(ma.iloc[-1]) == 18.0


class TestComputeEma:
    def test_ema_value(self):
        close = pd.Series([10.0] * 20)
        ema = compute_ema(close, 12)
        assert abs(float(ema.iloc[-1]) - 10.0) < 0.01


class TestCheckCondition:
    def test_greater_than(self):
        s = pd.Series([10, 20, 30, 40, 50])
        assert check_condition(s, ">", 25, 2) is True
        assert check_condition(s, ">", 35, 2) is False

    def test_less_than(self):
        s = pd.Series([50, 40, 30, 20, 10])
        assert check_condition(s, "<", 35, 2) is True

    def test_cross_above(self):
        s = pd.Series([10, 20, 30, 40, 50])
        assert check_condition(s, "cross_above", 25, 2) is True
        assert check_condition(s, "cross_above", 15, 2) is False

    def test_cross_below(self):
        s = pd.Series([50, 40, 30, 20, 10])
        assert check_condition(s, "cross_below", 35, 2) is True

    def test_out_of_bounds(self):
        s = pd.Series([10, 20])
        assert check_condition(s, ">", 5, -1) is False
        assert check_condition(s, ">", 5, 10) is False

    def test_equals(self):
        s = pd.Series([10, 20, 30])
        assert check_condition(s, "==", 20, 1) is True

    @pytest.mark.parametrize(
        "condition",
        [
            {"indicator": "rsi", "period": 14, "operator": ">=", "value": 50},
            {"indicator": "volume_ratio", "period": 5, "operator": "==", "value": 1},
            {"indicator": "bollinger_position", "period": 20, "operator": "==", "value": 0.5},
        ],
    )
    def test_warmup_values_do_not_satisfy_conditions(self, condition):
        df = pd.DataFrame({"close": list(range(100, 70, -1)), "volume": [1000] * 30})
        hit, _ = evaluate_conditions(df, [condition], "all", 0, {})
        assert hit is False

    def test_warmup_boundary_does_not_create_cross(self):
        df = pd.DataFrame({"close": list(range(100, 70, -1)), "volume": [1000] * 30})
        condition = {"indicator": "rsi", "period": 14, "operator": "cross_below", "value": 30}
        assert all(not evaluate_conditions(df, [condition], "all", i, {})[0] for i in range(len(df)))


class TestSubstitute:
    def test_simple(self):
        result = _substitute("{fast_period}", {"fast_period": 5})
        assert result == 5

    def test_no_match(self):
        result = _substitute("hello", {"fast_period": 5})
        assert result == "hello"

    def test_float(self):
        result = _substitute("{threshold}", {"threshold": 0.5})
        assert result == 0.5


class TestSampleCurve:
    def test_short_curve(self):
        curve = [{"date": f"d{i}", "equity": i} for i in range(10)]
        result = sample_curve(curve, 200)
        assert len(result) == 10

    def test_long_curve(self):
        curve = [{"date": f"d{i}", "equity": i} for i in range(500)]
        result = sample_curve(curve, 200)
        assert len(result) <= 201
        assert result[-1] == curve[-1]

    def test_evaluate_result_preserves_full_curve_metrics_with_null_ratio(self, tmp_path):
        metrics = {
            "total_trades": 0,
            "win_rate": 0,
            "profit_loss_ratio": None,
            "max_drawdown": -0.5,
            "total_return": 0,
            "sharpe_ratio": -1.23,
        }
        path = tmp_path / "result.json"
        path.write_text(
            json.dumps(
                {
                    "trades": [],
                    "equity_curve": [{"date": "2024-12-31", "equity": 1000}],
                    "initial_capital": 1000,
                    "final_capital": 1000,
                    "period": "2024-01-01 to 2024-12-31",
                    "metrics": metrics,
                }
            ),
            encoding="utf-8",
        )
        result = backtest.evaluate_result(str(path))
        assert result["metrics"] == metrics


class TestComputeMetrics:
    def test_basic_metrics(self):
        trades = [
            {"type": "buy", "date": "2024-01-01", "price": 10, "shares": 100, "amount": 1000},
            {
                "type": "sell",
                "date": "2024-02-01",
                "price": 12,
                "shares": 100,
                "amount": 1200,
                "pnl": 200,
                "pnl_pct": 0.2,
                "holding_days": 31,
            },
            {"type": "buy", "date": "2024-03-01", "price": 11, "shares": 100, "amount": 1100},
            {
                "type": "sell",
                "date": "2024-04-01",
                "price": 10,
                "shares": 100,
                "amount": 1000,
                "pnl": -100,
                "pnl_pct": -0.09,
                "holding_days": 31,
            },
        ]
        curve = [{"date": f"2024-01-{i + 1:02d}", "equity": 100000 + i * 10} for i in range(90)]
        metrics = compute_metrics(trades, curve, 100000, 110000, "2024-01-01", "2024-04-01")

        assert metrics["total_trades"] == 2
        assert metrics["winning_trades"] == 1
        assert metrics["losing_trades"] == 1
        assert abs(metrics["win_rate"] - 0.5) < 0.01
        assert metrics["total_return"] > 0

    def test_empty_trades(self):
        curve = [{"date": "2024-01-01", "equity": 100000}]
        metrics = compute_metrics([], curve, 100000, 100000, "2024-01-01", "2024-12-31")
        assert metrics["total_trades"] == 0
        assert metrics["win_rate"] == 0

    def test_all_win_profit_loss_ratio_is_not_a_currency_amount(self):
        trades = [{"type": "sell", "pnl": 1000, "holding_days": 1}]
        curve = [{"date": "2024-01-02", "equity": 11000}]
        metrics = compute_metrics(trades, curve, 10000, 11000, "2024-01-01", "2024-01-02")
        assert metrics["profit_loss_ratio"] == float("inf")
        assert _json_safe(metrics)["profit_loss_ratio"] is None

    @pytest.mark.parametrize("trades", [[], [{"type": "sell", "pnl": 0, "holding_days": 1}]])
    def test_profit_loss_ratio_is_unavailable_without_winners(self, trades):
        curve = [{"date": "2024-01-02", "equity": 10000}]
        metrics = compute_metrics(trades, curve, 10000, 10000, "2024-01-01", "2024-01-02")
        assert pd.isna(metrics["profit_loss_ratio"])
        assert _json_safe(metrics)["profit_loss_ratio"] is None


class TestDiagnose:
    def test_backtest_reports_actual_data_period(self, monkeypatch, make_kline_data):
        frame = pd.DataFrame(make_kline_data(40))
        frame["date"] = pd.date_range("2025-09-01", periods=40, freq="B")
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a: frame)
        monkeypatch.setattr(backtest, "load_strategy", lambda *a: {"entry": {"conditions": []}})
        result = backtest.run_backtest("unused", "AAPL", "2025-01-01", "2025-12-31", 1000)
        assert result["period"] == "2025-09-01 to 2025-10-24"
        assert result["requested_period"] == "2025-01-01 to 2025-12-31"
        assert result["warnings"]

    def test_initial_loss_is_included_in_drawdown(self):
        curve = [{"date": "2026-01-02", "equity": 99}, {"date": "2026-01-03", "equity": 99}]
        metrics = compute_metrics([], curve, 100, 99, "2026-01-01", "2026-01-03")
        assert metrics["max_drawdown"] == -0.01

    def test_good_strategy(self):
        metrics = {
            "win_rate": 0.65,
            "profit_loss_ratio": 2.5,
            "max_drawdown": -0.08,
            "sharpe_ratio": 1.8,
            "total_trades": 20,
            "winning_trades": 13,
            "losing_trades": 7,
            "avg_holding_days": 5,
            "max_consecutive_wins": 5,
            "max_consecutive_losses": 2,
            "total_return": 0.35,
            "annual_return": 0.35,
        }
        result = diagnose(metrics)
        assert len(result["strengths"]) > 0
        assert len(result["weaknesses"]) == 0

    def test_bad_strategy(self):
        metrics = {
            "win_rate": 0.3,
            "profit_loss_ratio": 0.8,
            "max_drawdown": -0.35,
            "sharpe_ratio": 0.3,
            "total_trades": 3,
            "winning_trades": 1,
            "losing_trades": 2,
            "avg_holding_days": 10,
            "max_consecutive_wins": 1,
            "max_consecutive_losses": 2,
            "total_return": -0.1,
            "annual_return": -0.1,
        }
        result = diagnose(metrics)
        assert len(result["weaknesses"]) > 0
        assert len(result["suggestions"]) > 0

    @pytest.mark.parametrize("plr", [None, float("nan")])
    def test_undefined_profit_loss_ratio_is_not_diagnosed(self, plr):
        metrics = {
            "win_rate": 0.5,
            "profit_loss_ratio": plr,
            "max_drawdown": -0.1,
            "sharpe_ratio": 0.5,
            "total_trades": 2,
            "winning_trades": 1,
            "losing_trades": 1,
            "avg_holding_days": 5,
            "max_consecutive_wins": 1,
            "max_consecutive_losses": 1,
            "total_return": 0.1,
            "annual_return": 0.1,
        }
        result = diagnose(metrics)
        messages = result["strengths"] + result["weaknesses"] + result["suggestions"]
        assert not any("盈亏比" in message for message in messages)

    def test_infinite_profit_loss_ratio_remains_a_strength(self):
        metrics = {
            "win_rate": 1,
            "profit_loss_ratio": float("inf"),
            "max_drawdown": -0.1,
            "sharpe_ratio": 0.5,
            "total_trades": 1,
            "winning_trades": 1,
            "losing_trades": 0,
            "avg_holding_days": 1,
            "max_consecutive_wins": 1,
            "max_consecutive_losses": 0,
            "total_return": 0.1,
            "annual_return": 0.1,
        }
        result = diagnose(metrics)
        assert "盈亏比优秀 (inf:1)" in result["strengths"]


_MA_CROSS_STRATEGY = {
    "name": "ma_cross",
    "entry": {
        "conditions": [{"indicator": "ma_diff", "period": 520, "operator": "cross_above", "value": 0}],
        "logic": "all",
    },
    "exit": {
        "conditions": [{"indicator": "ma_diff", "period": 520, "operator": "cross_below", "value": 0}],
        "logic": "any",
        "stop_loss": -0.08,
        "take_profit": 0.20,
    },
    "position": {"size": 1.0},
}


class TestMaDiffParams:
    def test_explicit_fast_slow(self):
        close = pd.Series([float(i) for i in range(1, 31)])
        diff = compute_ma_diff(close, fast=5, slow=10)
        # MA5 of 26..30 is 28, MA10 of 21..30 is 25.5
        assert float(diff.iloc[-1]) == 2.5

    def test_legacy_period_encoding_still_decodes(self):
        close = pd.Series([float(i) for i in range(1, 31)])
        pd.testing.assert_series_equal(compute_ma_diff(close, period=510), compute_ma_diff(close, fast=5, slow=10))
        pd.testing.assert_series_equal(compute_ma_diff(close, period=1020), compute_ma_diff(close, fast=10, slow=20))

    def test_legacy_zero_slow_fallback(self):
        close = pd.Series([float(i) for i in range(1, 31)])
        pd.testing.assert_series_equal(compute_ma_diff(close, period=500), compute_ma_diff(close, fast=5, slow=20))

    def test_default_pair_unchanged(self):
        close = pd.Series([float(i) for i in range(1, 31)])
        pd.testing.assert_series_equal(compute_ma_diff(close), compute_ma_diff(close, fast=5, slow=20))
        pd.testing.assert_series_equal(compute_ema_diff(close), compute_ema_diff(close, fast=12, slow=26))

    def test_three_digit_periods_now_expressible(self):
        # The legacy encoding (fast*100+slow) cannot express MA60-MA120; explicit params can.
        close = pd.Series([float(i) for i in range(1, 201)])
        diff = compute_ma_diff(close, fast=60, slow=120)
        expected = compute_ma(close, 60) - compute_ma(close, 120)
        pd.testing.assert_series_equal(diff, expected)

    def test_ema_diff_explicit(self):
        close = pd.Series([float(i) for i in range(1, 31)])
        diff = compute_ema_diff(close, fast=10, slow=30)
        expected = compute_ema(close, 10) - compute_ema(close, 30)
        pd.testing.assert_series_equal(diff, expected)


class TestEvaluateConditionsFastSlow:
    def test_fast_slow_matches_legacy_period(self):
        close = pd.Series([10.0] * 25 + [20.0] * 5)
        df = pd.DataFrame({"close": close, "volume": pd.Series([1e6] * 30)})
        legacy = [{"indicator": "ma_diff", "period": 510, "operator": ">", "value": 0}]
        explicit = [{"indicator": "ma_diff", "fast_period": 5, "slow_period": 10, "operator": ">", "value": 0}]
        hit_legacy, _ = evaluate_conditions(df, legacy, "all", 29, {})
        hit_explicit, reason = evaluate_conditions(df, explicit, "all", 29, {})
        assert hit_legacy is True
        assert hit_explicit is True
        assert "ma_diff(5,10)" in reason

    def test_fast_slow_string_values_coerced(self):
        # YAML parameter substitution can leave numbers as strings
        close = pd.Series([10.0] * 25 + [20.0] * 5)
        df = pd.DataFrame({"close": close, "volume": pd.Series([1e6] * 30)})
        cond = [{"indicator": "ma_diff", "fast_period": "5", "slow_period": "10", "operator": ">", "value": 0}]
        hit, _ = evaluate_conditions(df, cond, "all", 29, {})
        assert hit is True


class TestJsonSafe:
    def test_non_finite_becomes_none(self):
        payload = {
            "inf": float("inf"),
            "neg_inf": float("-inf"),
            "nan": float("nan"),
            "nested": [1.5, {"x": float("inf")}],
            "plain": "text",
            "integer": 3,
        }
        safe = _json_safe(payload)
        assert safe["inf"] is None
        assert safe["neg_inf"] is None
        assert safe["nan"] is None
        assert safe["nested"][1]["x"] is None
        assert safe["plain"] == "text"
        assert safe["integer"] == 3
        # Strict encoders reject non-finite floats; sanitized payload must pass.
        json.dumps(safe, allow_nan=False)

    def test_all_zero_pnl_is_undefined_then_sanitized(self):
        trades = [
            {"type": "buy", "date": "2024-01-01", "price": 10, "shares": 100, "amount": 1000},
            {
                "type": "sell",
                "date": "2024-01-10",
                "price": 10,
                "shares": 100,
                "amount": 1000,
                "pnl": 0,
                "pnl_pct": 0.0,
                "holding_days": 9,
            },
        ]
        curve = [{"date": f"2024-01-{i + 1:02d}", "equity": 100000} for i in range(30)]
        metrics = compute_metrics(trades, curve, 100000, 100000, "2024-01-01", "2024-01-31")
        assert pd.isna(metrics["profit_loss_ratio"])
        assert _json_safe(metrics)["profit_loss_ratio"] is None

    def test_main_stdout_is_strict_json(self, monkeypatch, capsys):
        monkeypatch.setattr(
            backtest,
            "run_backtest",
            lambda *a, **kw: {"metrics": {"profit_loss_ratio": float("inf")}, "worst": float("nan")},
        )
        monkeypatch.setattr(sys, "argv", ["backtest.py", "run", "unused.yaml", "600000"])
        backtest.main()
        out = capsys.readouterr().out
        assert "Infinity" not in out
        assert "NaN" not in out
        parsed = json.loads(out, parse_constant=_reject_json_constants)
        assert parsed["metrics"]["profit_loss_ratio"] is None
        assert parsed["worst"] is None

    def test_main_fetch_failure_prints_error_json(self, monkeypatch, capsys):
        # fetch_kline raises when the kline subprocess fails (run_tool → None);
        # stdout must still be a parseable {"error": ...} document.
        monkeypatch.setattr(backtest, "run_tool", lambda *a, **kw: None)
        strategy = Path(__file__).parent.parent / "strategies" / "examples" / "ma_crossover.yaml"
        monkeypatch.setattr(sys, "argv", ["backtest.py", "run", str(strategy), "600000"])
        with pytest.raises(SystemExit) as exc:
            backtest.main()
        assert exc.value.code == 1
        parsed = json.loads(capsys.readouterr().out, parse_constant=_reject_json_constants)
        assert "error" in parsed


def _reject_json_constants(value):
    raise ValueError(f"non-standard JSON constant in output: {value}")


# Reference copies of the pre-refactor SIGNAL_DEFINITIONS lambdas, which recomputed
# the full indicator series inside the checker on every bar (O(n²)). The optimized
# implementation must produce identical verdicts from precomputed series.
_LEGACY_SIGNAL_DEFINITIONS = {
    "macd_golden_cross": lambda df, i: (
        i >= 1
        and float(compute_macd_dif(df["close"]).iloc[i]) > float(compute_macd_dea(df["close"]).iloc[i])
        and float(compute_macd_dif(df["close"]).iloc[i - 1]) <= float(compute_macd_dea(df["close"]).iloc[i - 1])
    ),
    "macd_death_cross": lambda df, i: (
        i >= 1
        and float(compute_macd_dif(df["close"]).iloc[i]) < float(compute_macd_dea(df["close"]).iloc[i])
        and float(compute_macd_dif(df["close"]).iloc[i - 1]) >= float(compute_macd_dea(df["close"]).iloc[i - 1])
    ),
    "rsi_oversold": lambda df, i: float(compute_rsi(df["close"], 14).iloc[i]) < 30,
    "rsi_overbought": lambda df, i: float(compute_rsi(df["close"], 14).iloc[i]) > 70,
    "breakout_20d": lambda df, i: (
        i >= 20
        and float(df["close"].iloc[i]) > float(df["high"].iloc[i - 20 : i].max())
        and float(df["close"].iloc[i - 1]) <= float(df["high"].iloc[i - 20 : i].max())
    ),
    "breakdown_20d": lambda df, i: (
        i >= 20
        and float(df["close"].iloc[i]) < float(df["low"].iloc[i - 20 : i].min())
        and float(df["close"].iloc[i - 1]) >= float(df["low"].iloc[i - 20 : i].min())
    ),
    "volume_surge": lambda df, i: (
        i >= 20 and float(df["volume"].iloc[i]) > 2.0 * float(df["volume"].iloc[i - 20 : i].mean())
    ),
    "ma_golden_cross": lambda df, i: (
        i >= 20
        and float(compute_ma(df["close"], 5).iloc[i]) > float(compute_ma(df["close"], 20).iloc[i])
        and float(compute_ma(df["close"], 5).iloc[i - 1]) <= float(compute_ma(df["close"], 20).iloc[i - 1])
    ),
    "ma_death_cross": lambda df, i: (
        i >= 20
        and float(compute_ma(df["close"], 5).iloc[i]) < float(compute_ma(df["close"], 20).iloc[i])
        and float(compute_ma(df["close"], 5).iloc[i - 1]) >= float(compute_ma(df["close"], 20).iloc[i - 1])
    ),
}


class TestEvaluateSignalPrecompute:
    @pytest.mark.parametrize("signal_name", list(SIGNAL_DEFINITIONS))
    def test_precomputed_context_matches_legacy_per_bar(self, signal_name, make_kline_data):
        df = pd.DataFrame(make_kline_data(100, "volatile"))
        ctx = backtest._signal_context(df)
        checker = SIGNAL_DEFINITIONS[signal_name]
        legacy = _LEGACY_SIGNAL_DEFINITIONS[signal_name]
        for i in (20, 40, 60, 99):
            assert checker(ctx, i) == legacy(df, i)

    def test_evaluate_signal_matches_legacy_loop(self, monkeypatch, make_kline_data):
        df = pd.DataFrame(make_kline_data(120, "volatile"))
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a, **kw: df)
        forward_days = [3, 5, 10]
        max_fwd = max(forward_days)
        for name in ("ma_golden_cross", "macd_golden_cross", "breakout_20d", "rsi_oversold", "volume_surge"):
            result = evaluate_signal("600000", name, forward_days, 250)
            legacy = _LEGACY_SIGNAL_DEFINITIONS[name]
            expected_hits = [i for i in range(20, len(df) - max_fwd) if legacy(df, i)]
            assert result["signal"] == name
            assert result.get("occurrences", 0) == len(expected_hits)
            assert [s["date"] for s in result.get("samples", [])] == [
                str(df["date"].iloc[i].date()) for i in expected_hits[-10:]
            ]

    def test_context_computed_once_per_call(self, monkeypatch, make_kline_data):
        df = pd.DataFrame(make_kline_data(100, "volatile"))
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a, **kw: df)
        original = backtest._signal_context
        calls = []
        monkeypatch.setattr(backtest, "_signal_context", lambda d: calls.append(1) or original(d))
        evaluate_signal("600000", "rsi_oversold", [3, 5, 10], 250)
        assert len(calls) == 1


class TestRunBacktest:
    def test_end_to_end(self, monkeypatch, make_kline_data):
        df = pd.DataFrame(make_kline_data(120, "volatile"))
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a, **kw: df)
        monkeypatch.setattr(backtest, "load_strategy", lambda *a, **kw: _MA_CROSS_STRATEGY)

        result = backtest.run_backtest("unused.yaml", "600000", "2024-01-01", "2024-06-30", 1000000)

        assert "metrics" in result
        assert "diagnosis" in result
        assert "diagnostics" in result

    def test_monthly_momentum_252_increases_fetch_count_by_required_bars(self, monkeypatch, make_kline_data):
        rows = make_kline_data(90)
        counts = []

        def fake_run(_script, args, **_kwargs):
            counts.append(int(args[args.index("--count") + 1]))
            return rows

        monkeypatch.setattr(backtest, "run_tool", fake_run)
        monkeypatch.setattr(
            backtest,
            "load_strategy",
            lambda *a: {
                "entry": {"conditions": [{"indicator": "monthly_momentum", "period": 252, "operator": ">", "value": 0}]}
            },
        )
        start, end = str(rows[40]["date"].date()), str(rows[80]["date"].date())
        backtest.fetch_kline("AAPL", start, end, True)
        backtest.run_backtest("unused.yaml", "AAPL", start, end, 10000)

        assert counts[1] - counts[0] == 253

    def test_monthly_momentum_midmonth_start_uses_prior_monthly_observation_via_cli(
        self, monkeypatch, tmp_path, capsys, make_kline_data
    ):
        import yaml

        dates = pd.bdate_range("2023-01-02", "2024-03-28")
        rows = make_kline_data(len(dates))
        for row, stamp, close in zip(rows, dates, range(100, 100 + len(dates)), strict=True):
            row.update(date=stamp, open=close, close=close, high=close + 1, low=close - 1)
        monkeypatch.setattr(
            backtest,
            "run_tool",
            lambda _script, args, **_kwargs: rows[-int(args[args.index("--count") + 1]) :],
        )
        strategy = {
            "entry": {"conditions": [{"indicator": "monthly_momentum", "period": 5, "operator": ">", "value": 0}]},
            "exit": {"stop_loss": -1, "take_profit": 1000},
            "simulation": {"execution": "next_open", "slippage": 0, "commission": 0},
        }
        path = tmp_path / "monthly.yaml"
        path.write_text(yaml.safe_dump(strategy), encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            ["backtest.py", "run", str(path), "AAPL", "--start", "2024-02-15", "--end", "2024-03-28"],
        )

        backtest.main()

        result = json.loads(capsys.readouterr().out)
        assert result["trades"][0]["date"] == "2024-02-15"

    def test_monthly_momentum_252_warns_when_first_window_signal_still_has_nan(self, monkeypatch, make_kline_data):
        df = pd.DataFrame(make_kline_data(90))
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a: df)
        monkeypatch.setattr(
            backtest,
            "load_strategy",
            lambda *a: {
                "entry": {"conditions": [{"indicator": "monthly_momentum", "period": 252, "operator": ">", "value": 0}]}
            },
        )
        start, end = str(df["date"].iloc[40].date()), str(df["date"].iloc[80].date())
        result = backtest.run_backtest("unused.yaml", "AAPL", start, end, 10000)

        assert "error" not in result
        assert any(
            "Entry indicator is unavailable" in warning and "monthly_momentum" in warning and "252" in warning
            for warning in result["warnings"]
        )

    def test_zero_width_bollinger_is_not_misreported_as_insufficient_warmup(self, monkeypatch):
        df = _df_from_closes([100] * 40)
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a: df)
        monkeypatch.setattr(
            backtest,
            "load_strategy",
            lambda *a: {"entry": {"conditions": [{"indicator": "bollinger_position", "operator": ">", "value": 0}]}},
        )
        start, end = str(df["date"].iloc[10].date()), str(df["date"].iloc[-1].date())
        result = backtest.run_backtest("unused", "AAPL", start, end, 10000)

        assert any("Entry indicator is unavailable" in warning for warning in result["warnings"])
        assert all("Insufficient warmup" not in warning for warning in result["warnings"])

    @pytest.mark.parametrize(
        "condition",
        [
            {"indicator": "ma", "period": 20, "operator": ">", "value": 0},
            {"indicator": "ema_diff", "fast_period": 2, "slow_period": 4, "operator": ">", "value": 0},
            {"indicator": "macd_dif", "operator": ">", "value": 0},
            {"indicator": "monthly_momentum", "period": 5, "operator": ">", "value": 0},
        ],
    )
    def test_prehistory_warms_indicators_without_entering_the_evaluation_window(
        self, monkeypatch, tmp_path, condition, make_kline_data
    ):
        import yaml

        rows = make_kline_data(80)
        for i, row in enumerate(rows):
            row.update(open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1000)
        dates = pd.DatetimeIndex(row["date"] for row in rows)
        monkeypatch.setattr(backtest, "run_tool", lambda *args, **kwargs: rows)
        strategy = {
            "entry": {"conditions": [condition]},
            "exit": {"stop_loss": -1, "take_profit": 1000},
            "simulation": {"execution": "next_open", "slippage": 0, "commission": 0},
        }
        path = tmp_path / "warmup.yaml"
        path.write_text(yaml.safe_dump(strategy), encoding="utf-8")
        start, end = dates[40].strftime("%Y-%m-%d"), dates[70].strftime("%Y-%m-%d")

        result = backtest.run_backtest(str(path), "AAPL", start, end, 10000)

        assert result["trades"][0]["date"] == start
        assert result["equity_curve"][0]["date"] == start
        assert result["period"] == f"{start} to {end}"
        reference_frame = pd.DataFrame(rows)
        reference_frame = reference_frame[reference_frame["date"] <= end].reset_index(drop=True)
        reference = backtest.simulate(reference_frame, strategy, 10000, "AAPL", start=start)
        assert result["trades"] == reference["trades"]
        assert result["equity_curve"] == backtest.sample_curve(reference["equity_curve"])
        assert result["final_capital"] == reference["final_equity"]
        assert all(trade["date"] >= start for trade in result["trades"])

    @pytest.mark.parametrize(
        "yaml_name",
        sorted(p.name for p in (Path(__file__).parent.parent / "strategies" / "examples").glob("*.yaml")),
    )
    def test_example_yamls_load_and_run(self, monkeypatch, make_kline_data, yaml_name):
        # Every shipped example must load and run as-is (the ma_crossover one
        # exercises the {fast_period}/{slow_period} parameter substitution).
        df = pd.DataFrame(make_kline_data(120, "volatile"))
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a, **kw: df)
        yaml_path = Path(__file__).parent.parent / "strategies" / "examples" / yaml_name

        result = backtest.run_backtest(str(yaml_path), "600000", "2024-01-01", "2024-06-30", 1000000)

        assert "error" not in result
        assert "metrics" in result


def _df_from_closes(closes) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1e6] * n,
        }
    )


_ALWAYS_ENTRY = {"conditions": [{"indicator": "close", "operator": ">", "value": 0}], "logic": "all"}


class TestTrailingStop:
    def test_trailing_stop_triggers_on_drawdown_from_peak(self):
        # Peak close 14 at bar 5; trail level = 14 * (1 - 0.08) = 12.88, bar 6 close 12.6 triggers.
        df = _df_from_closes([10, 10, 11, 12, 13, 14, 12.6])
        strategy = {
            "simulation": {"execution": "same_close"},
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 5.0, "trailing_stop": 0.08},
            "position": {"size": 1.0},
        }
        sim = backtest.simulate(df, strategy, 100000, "600000")
        sells = [t for t in sim["trades"] if t["type"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["reason"].startswith("移动止损触发")
        assert sells[0]["date"] == str(df["date"].iloc[6].date())
        assert sells[0]["pnl"] > 0  # sold above the entry despite being a stop

    def test_no_trailing_stop_key_holds_through_dip(self):
        df = _df_from_closes([10, 10, 11, 12, 13, 14, 12.6, 12.0, 11.0])
        strategy = {
            "simulation": {"execution": "same_close"},
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 5.0},
            "position": {"size": 1.0},
        }
        sim = backtest.simulate(df, strategy, 100000, "600000")
        assert [t for t in sim["trades"] if t["type"] == "sell"] == []

    def test_fixed_stop_loss_takes_precedence_when_both_trigger(self):
        # Bar 3 close 8 is both below the fixed stop (-20% vs entry) and the 8% trail from peak 14.
        df = _df_from_closes([10, 10, 14, 8, 9, 9])
        strategy = {
            "simulation": {"execution": "same_close"},
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.10, "take_profit": 5.0, "trailing_stop": 0.08},
            "position": {"size": 1.0},
        }
        sim = backtest.simulate(df, strategy, 100000, "600000")
        sells = [t for t in sim["trades"] if t["type"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["reason"].startswith("止损触发")
        assert not sells[0]["reason"].startswith("移动止损")

    def test_take_profit_fires_on_fresh_high_when_trailing_configured(self):
        # The trailing check sits ahead of take_profit in the elif chain but can
        # never fire on a fresh-high bar (price > peak*(1-ts) by construction), so a
        # bar crossing the tp threshold on a new high resolves to 止盈. (Trailing and
        # tp can never co-trigger on one bar: peak can never exceed buy*(1+tp) while
        # holding — any such close would have fired tp on that earlier bar.)
        df = _df_from_closes([100, 100, 110, 130])
        strategy = {
            "simulation": {"execution": "same_close"},
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 0.20, "trailing_stop": 0.08},
            "position": {"size": 1.0},
        }
        sim = backtest.simulate(df, strategy, 100000, "600000")
        sells = [t for t in sim["trades"] if t["type"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["reason"].startswith("止盈触发")
        assert sells[0]["date"] == str(df["date"].iloc[3].date())

    def test_trailing_stop_peak_resets_after_rebuy(self):
        # peak_close is per-position: after position 1 peaks at 150 and is stopped
        # out, position 2 (re-entry at 100) must trail from its own peak. Bar 5
        # (close 120) is the discriminator — a leaked 150 peak would trail-stop
        # there (120 <= 150*0.9); a correctly reset peak (120) does not.
        df = _df_from_closes([100, 100, 150, 95, 100, 120, 125, 110])
        strategy = {
            "simulation": {"execution": "same_close"},
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 5.0, "trailing_stop": 0.10},
            "position": {"size": 1.0},
        }
        sim = backtest.simulate(df, strategy, 100000, "600000")
        sells = [t for t in sim["trades"] if t["type"] == "sell"]
        assert len(sells) == 2
        assert sells[0]["date"] == str(df["date"].iloc[3].date())
        assert sells[0]["reason"].startswith("移动止损触发")
        # position 2 survives bar 5 (120) and bar 6 (125), stops out on bar 7
        # (110 <= 125*0.9 = 112.5) — trailing from its own peak, not the old 150.
        assert sells[1]["date"] == str(df["date"].iloc[7].date())
        assert sells[1]["reason"].startswith("移动止损触发")


class TestStrategyValidation:
    @pytest.mark.parametrize("side", ["entry", "exit"])
    def test_condition_logic_must_be_all_or_any(self, side):
        with pytest.raises(ValueError, match="logic"):
            backtest.simulate(_df_from_closes([10, 10, 12]), {side: {"logic": "xor"}}, 1000, "AAPL")

    @pytest.mark.parametrize("side", ["entry", "exit"])
    def test_condition_operator_must_be_documented(self, side):
        condition = {"indicator": "close", "operator": "!=", "value": 0}
        strategy = {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 1000}]}}
        strategy[side] = {"conditions": [condition]}
        with pytest.raises(ValueError, match="operator"):
            backtest.simulate(_df_from_closes([10, 10, 12]), strategy, 1000, "AAPL")

    def test_untriggered_exit_indicator_is_validated_at_startup(self):
        strategy = {
            "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 1000}]},
            "exit": {"conditions": [{"indicator": "not_an_indicator", "operator": ">", "value": 0}]},
        }
        with pytest.raises(ValueError, match="Unknown indicator"):
            backtest.simulate(_df_from_closes([10, 10, 12]), strategy, 1000, "AAPL")

    @pytest.mark.parametrize("parameter", ["period", "fast_period", "slow_period"])
    @pytest.mark.parametrize("invalid", [True, 0, -1, 2.5, "2.5", "invalid", None])
    def test_untriggered_exit_rejects_non_positive_integer_periods(self, parameter, invalid):
        condition = {"indicator": "rsi", "operator": ">", "value": 50, parameter: invalid}
        strategy = {
            "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 1000}]},
            "exit": {"conditions": [condition]},
        }
        with pytest.raises(ValueError, match=parameter):
            backtest.simulate(_df_from_closes([10, 10, 12]), strategy, 1000, "AAPL")

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "not-a-number"])
    def test_run_backtest_rejects_invalid_condition_values_before_entry(self, monkeypatch, value):
        df = _df_from_closes([10.0] * 30)
        monkeypatch.setattr(backtest, "fetch_kline", lambda *args: df)
        strategy = {
            "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 1000}]},
            "exit": {"conditions": [{"indicator": "close", "operator": ">", "value": value}]},
        }
        monkeypatch.setattr(backtest, "load_strategy", lambda *args: strategy)
        start, end = (str(df["date"].iloc[i].date()) for i in (0, -1))

        with pytest.raises(ValueError, match="condition value"):
            backtest.run_backtest("unused", "AAPL", start, end, 10000)

    @pytest.mark.parametrize("side", ["entry", "exit"])
    @pytest.mark.parametrize("value", [True, False])
    def test_run_backtest_rejects_boolean_condition_values(self, monkeypatch, side, value):
        df = _df_from_closes([10.0] * 30)
        monkeypatch.setattr(backtest, "fetch_kline", lambda *args: df)
        strategy = {
            "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 1000}]},
            "exit": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]},
        }
        strategy[side] = {"conditions": [{"indicator": "close", "operator": ">", "value": value}]}
        monkeypatch.setattr(backtest, "load_strategy", lambda *args: strategy)
        start, end = (str(df["date"].iloc[i].date()) for i in (0, -1))

        with pytest.raises(ValueError, match="condition value"):
            backtest.run_backtest("unused", "AAPL", start, end, 10000)

    @pytest.mark.parametrize(
        "indicator",
        [
            "rsi",
            "ma",
            "ema",
            "macd_dif",
            "macd_dea",
            "macd",
            "ma_diff",
            "ema_diff",
            "trend_score",
            "monthly_momentum",
            "volume_ratio",
            "price_change",
            "bollinger_position",
            "close",
            "volume",
        ],
    )
    def test_every_indicator_rejects_fractional_period_strings_at_startup(self, indicator):
        condition = {"indicator": indicator, "operator": ">", "value": 0, "period": "2.5"}
        strategy = {
            "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 1000}]},
            "exit": {"conditions": [condition]},
        }
        with pytest.raises(ValueError, match="period"):
            backtest.simulate(_df_from_closes([10, 10, 12]), strategy, 1000, "AAPL")

    def test_invalid_period_cli_prints_json_error(self, monkeypatch, capsys):
        df = _df_from_closes([10.0] * 40)
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a: df)
        monkeypatch.setattr(
            backtest,
            "load_strategy",
            lambda *a: {"entry": {"conditions": [{"indicator": "ma", "period": "2.5", "operator": ">", "value": 0}]}},
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["backtest.py", "run", "unused.yaml", "AAPL", "--start", "2024-01-01", "--end", "2024-02-23"],
        )
        with pytest.raises(SystemExit) as exc:
            backtest.main()
        assert exc.value.code == 1
        assert "period" in json.loads(capsys.readouterr().out)["error"]

    def test_simulate_rejects_unknown_adjustment_basis(self):
        df = _df_from_closes([10, 10, 12]).assign(adjusted="unknown")
        with pytest.raises(ValueError, match="unknown adjustment basis"):
            backtest.simulate(df, {"entry": _ALWAYS_ENTRY}, 1000, "AAPL")

    def test_simulate_rejects_mixed_adjustment_basis(self):
        df = _df_from_closes([10, 10, 12]).assign(adjusted=["qfq", "qfq", "none"])
        with pytest.raises(ValueError, match="single adjustment basis"):
            backtest.simulate(df, {"entry": _ALWAYS_ENTRY}, 1000, "AAPL")

    @pytest.mark.parametrize("basis", [None, "qfq", "total_return", "none"])
    def test_simulate_allows_missing_or_single_known_adjustment_basis(self, basis):
        df = _df_from_closes([10, 10, 12])
        if basis is not None:
            df["adjusted"] = basis
        result = backtest.simulate(df, {"entry": _ALWAYS_ENTRY}, 1000, "AAPL")
        assert result["trades"]

    @pytest.mark.parametrize(
        ("config", "field"),
        [
            ({"stop_loss": 0}, "stop_loss"),
            ({"stop_loss": 0.05}, "stop_loss"),
            ({"take_profit": 0}, "take_profit"),
            ({"take_profit": -0.2}, "take_profit"),
            ({"trailing_stop": 0}, "trailing_stop"),
            ({"trailing_stop": -0.1}, "trailing_stop"),
            ({"trailing_stop": 1}, "trailing_stop"),
        ],
    )
    def test_exit_thresholds_reject_wrong_sign_or_range(self, config, field):
        with pytest.raises(ValueError, match=field):
            backtest.simulate(_df_from_closes([10, 10, 12]), {"exit": config}, 100000, "AAPL")

    def test_multiple_positions_are_rejected_instead_of_ignored(self):
        with pytest.raises(ValueError, match="max_positions"):
            backtest.simulate(_df_from_closes([10, 10]), {"position": {"max_positions": 2}}, 1000, "AAPL")


class TestStampTax:
    @pytest.mark.parametrize("size", [-1, 1.1, float("nan")])
    def test_position_fraction_cannot_create_borrowed_cash(self, size):
        with pytest.raises(ValueError, match="position.size"):
            backtest.simulate(_df_from_closes([10, 10]), {"position": {"size": size}}, 1000, "AAPL")

    @pytest.mark.parametrize("symbol", ["600519", "sh600519", "sz000001"])
    def test_a_share_aliases_use_same_lot_size(self, symbol):
        strategy = {"entry": _ALWAYS_ENTRY, "simulation": {"execution": "same_close", "slippage": 0, "commission": 0}}
        sim = backtest.simulate(_df_from_closes([10, 10]), strategy, 1500, symbol)
        assert sim["trades"][0]["shares"] == 100

    def test_full_position_includes_buy_commission_in_budget(self):
        strategy = {
            "entry": _ALWAYS_ENTRY,
            "position": {"size": 1},
            "simulation": {"slippage": 0, "commission": 0.0015},
        }
        sim = backtest.simulate(_df_from_closes([10, 10, 10]), strategy, 100, "AAPL")
        assert sim["trades"][0]["shares"] == 9
        assert sim["trades"][0]["amount"] <= 100

    def test_stamp_tax_deducted_from_sell_proceeds_only(self):
        # Entry at close 10 (bar 1), take-profit at close 12 (bar 3, +20%).
        # One million covers 99,900 shares plus commission; sell tax applies only at exit.
        df = _df_from_closes([10, 10, 11, 12])
        strategy = {
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 0.20},
            "position": {"size": 1.0},
            "simulation": {"execution": "same_close", "slippage": 0.0, "commission": 0.001, "stamp_tax": 0.0005},
        }
        sim = backtest.simulate(df, strategy, 1000000, "600000")
        buys = [t for t in sim["trades"] if t["type"] == "buy"]
        sells = [t for t in sim["trades"] if t["type"] == "sell"]
        assert len(buys) == 1 and len(sells) == 1
        assert buys[0]["shares"] == 99900
        assert buys[0]["amount"] == 999999.0  # stamp tax never touches the buy side
        assert sells[0]["price"] == 12.0
        assert sells[0]["amount"] == 1197001.8
        assert sim["costs"]["commission_paid"] == pytest.approx(2197.8)
        assert sim["costs"]["stamp_tax_paid"] == pytest.approx(599.4)

    def test_default_stamp_tax_zero_matches_explicit_zero(self):
        # No simulation block must behave exactly like stamp_tax: 0 with default rates.
        df = _df_from_closes([10, 10, 11, 12, 13, 14, 12.6])
        base = {
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 0.20},
            "position": {"size": 1.0},
        }
        implicit = backtest.simulate(df, dict(base), 100000, "600000")
        explicit = backtest.simulate(
            df,
            dict(base, simulation={"slippage": 0.001, "commission": 0.0015, "stamp_tax": 0.0}),
            100000,
            "600000",
        )
        assert implicit["trades"] == explicit["trades"]
        assert implicit["costs"]["stamp_tax"] == 0.0
        assert implicit["costs"]["stamp_tax_paid"] == 0.0
        assert implicit["costs"]["commission_paid"] == explicit["costs"]["commission_paid"] > 0

    def test_costs_echoed_in_run_backtest_result(self, monkeypatch, make_kline_data):
        df = pd.DataFrame(make_kline_data(120, "volatile"))
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a, **kw: df)
        strategy = dict(_MA_CROSS_STRATEGY, simulation={"slippage": 0.001, "commission": 0.00025, "stamp_tax": 0.0005})
        monkeypatch.setattr(backtest, "load_strategy", lambda *a, **kw: strategy)

        result = backtest.run_backtest("unused.yaml", "600000", "2024-01-01", "2024-06-30", 1000000)

        costs = result["costs"]
        assert costs["slippage"] == 0.001
        assert costs["commission"] == 0.00025
        assert costs["stamp_tax"] == 0.0005
        assert costs["commission_paid"] >= 0
        assert costs["stamp_tax_paid"] >= 0

    @pytest.mark.parametrize("field", ["slippage", "commission", "stamp_tax"])
    @pytest.mark.parametrize("bad", [-0.001, 1, 5])
    def test_out_of_range_rate_rejected(self, field, bad):
        # Orders-of-magnitude entry errors (e.g. 5 instead of 0.0005) must fail
        # loudly, not silently produce absurd proceeds; main() turns this ValueError
        # into a JSON error.
        df = _df_from_closes([10, 10, 12])
        strategy = {
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 0.20},
            "position": {"size": 1.0},
            "simulation": {field: bad},
        }
        with pytest.raises(ValueError, match=field):
            backtest.simulate(df, strategy, 100000, "600000")

    def test_combined_sell_rates_must_stay_below_one(self):
        # Each rate passes [0,1) alone, but commission + stamp_tax >= 1 makes sell
        # proceeds non-positive — reject the combination.
        df = _df_from_closes([10, 10, 12])
        strategy = {
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 0.20},
            "position": {"size": 1.0},
            "simulation": {"commission": 0.6, "stamp_tax": 0.6},
        }
        with pytest.raises(ValueError, match="commission"):
            backtest.simulate(df, strategy, 100000, "600000")

    def test_empty_string_stamp_tax_rejected(self):
        # "" must error loudly via float(), not silently disable the tax.
        df = _df_from_closes([10, 10, 12])
        strategy = {
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 0.20},
            "position": {"size": 1.0},
            "simulation": {"stamp_tax": ""},
        }
        with pytest.raises(ValueError, match="could not convert"):
            backtest.simulate(df, strategy, 100000, "600000")

    def test_empty_stamp_tax_value_means_disabled(self):
        # YAML `stamp_tax:` with no value parses as None → treated as 0 (disabled).
        df = _df_from_closes([10, 10, 11, 12])
        strategy = {
            "entry": _ALWAYS_ENTRY,
            "exit": {"stop_loss": -0.5, "take_profit": 0.20},
            "position": {"size": 1.0},
            "simulation": {"slippage": 0.0, "commission": 0.001, "stamp_tax": None},
        }
        sim = backtest.simulate(df, strategy, 1000000, "600000")
        assert sim["costs"]["stamp_tax"] == 0.0
        assert sim["costs"]["stamp_tax_paid"] == 0.0


class TestBenchmark:
    def test_benchmark_block_added(self, monkeypatch, make_kline_data):
        main_df = pd.DataFrame(make_kline_data(120, "volatile"))
        bm_df = pd.DataFrame(make_kline_data(120, "up", base=4000.0))
        dfs = {"600000": main_df, "000300.SH": bm_df}
        monkeypatch.setattr(
            backtest,
            "fetch_kline",
            lambda symbol, start, end, *a, **kw: dfs[symbol][(dfs[symbol].date >= start) & (dfs[symbol].date <= end)],
        )
        monkeypatch.setattr(backtest, "load_strategy", lambda *a, **kw: dict(_MA_CROSS_STRATEGY, benchmark="000300.SH"))

        result = backtest.run_backtest("unused.yaml", "600000", "2024-01-01", "2024-06-30", 1000000)

        bm = result["benchmark"]
        assert bm["symbol"] == "000300.SH"
        first, last = float(bm_df["open"].iloc[1]), float(bm_df["close"].iloc[-1])
        expected = (last - first) / first
        assert bm["total_return"] == pytest.approx(expected, abs=1e-3)
        assert bm["excess_return"] == pytest.approx(result["metrics"]["total_return"] - expected, abs=1e-3)

    @pytest.mark.parametrize(("execution", "expected"), [("next_open", 0.5), ("same_close", 0.2)])
    def test_benchmark_entry_price_matches_execution(self, monkeypatch, execution, expected):
        df = _df_from_closes([100, 120])
        df["open"] = [80, 110]
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a: df)

        result = backtest._benchmark_comparison("BENCH", "2024-01-01", "2024-01-02", 0, execution)

        assert result["total_return"] == pytest.approx(expected)
        assert result["excess_return"] == pytest.approx(-expected)

    @pytest.mark.parametrize("execution", ["next_open", "same_close"])
    def test_benchmark_starts_when_strategy_can_trade_without_prehistory(self, monkeypatch, execution):
        df = _df_from_closes([50] + [100] * 29)
        monkeypatch.setattr(
            backtest, "fetch_kline", lambda symbol, start, end, *a, **kwargs: df[(df.date >= start) & (df.date <= end)]
        )
        strategy = {
            "benchmark": "BENCH",
            "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]},
            "simulation": {"execution": execution, "slippage": 0, "commission": 0},
        }
        monkeypatch.setattr(backtest, "load_strategy", lambda *a: strategy)
        start, end = str(df["date"].iloc[0].date()), str(df["date"].iloc[-1].date())

        result = backtest.run_backtest("unused", "AAPL", start, end, 10000)

        assert result["benchmark"]["total_return"] == 0
        assert result["benchmark"]["excess_return"] == result["metrics"]["total_return"] == 0

    @pytest.mark.parametrize(("start", "end"), [("2023-12-31", "2024-01-02"), ("2024-01-01", "2024-01-03")])
    def test_benchmark_rejects_short_coverage_without_excess(self, monkeypatch, start, end):
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a: _df_from_closes([100, 120]))

        result = backtest._benchmark_comparison("BENCH", start, end, 0.1, "same_close")

        assert "error" in result
        assert "excess_return" not in result

    @pytest.mark.parametrize(
        ("column", "value"), [("open", 0), ("close", 0), ("open", float("inf")), ("close", float("nan"))]
    )
    def test_benchmark_rejects_non_positive_or_non_finite_boundary_prices(self, monkeypatch, column, value):
        df = _df_from_closes([100.0, 120.0])
        df.loc[0 if column == "open" else df.index[-1], column] = value
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a: df)

        result = backtest._benchmark_comparison("BENCH", "2024-01-01", "2024-01-02", 0.1)

        assert "error" in result
        assert "excess_return" not in result

    @pytest.mark.parametrize("basis", ["unknown", "mixed"])
    def test_benchmark_rejects_unverified_adjustment_basis(self, monkeypatch, make_kline_data, basis):
        main_df = pd.DataFrame(make_kline_data(30)).assign(adjusted="total_return")
        bm_df = pd.DataFrame(make_kline_data(30, base=4000.0))
        bm_df["adjusted"] = "unknown" if basis == "unknown" else ["total_return"] * 15 + ["none"] * 15
        dfs = {"AAPL": main_df, "BENCH": bm_df}

        def fake_fetch(symbol, start, end, *args, **kwargs):
            df = dfs[symbol]
            return df[(df.date >= start) & (df.date <= end)].copy()

        monkeypatch.setattr(backtest, "fetch_kline", fake_fetch)
        strategy = {
            "benchmark": "BENCH",
            "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 0}]},
        }
        monkeypatch.setattr(backtest, "load_strategy", lambda *args: strategy)
        start, end = (str(main_df["date"].iloc[i].date()) for i in (0, -1))

        result = backtest.run_backtest("unused", "AAPL", start, end, 10000)

        assert "adjusted" in result["benchmark"]["error"]
        assert "total_return" not in result["benchmark"]
        assert "excess_return" not in result["benchmark"]

    def test_no_benchmark_key_output_unchanged(self, monkeypatch, make_kline_data):
        df = pd.DataFrame(make_kline_data(120, "volatile"))
        monkeypatch.setattr(backtest, "fetch_kline", lambda *a, **kw: df)
        monkeypatch.setattr(backtest, "load_strategy", lambda *a, **kw: _MA_CROSS_STRATEGY)

        result = backtest.run_backtest("unused.yaml", "600000", "2024-01-01", "2024-06-30", 1000000)

        assert "benchmark" not in result

    def test_benchmark_fetch_failure_degrades_to_error_block(self, monkeypatch, make_kline_data):
        main_df = pd.DataFrame(make_kline_data(120, "volatile"))

        def fake_fetch(symbol, *a, **kw):
            if symbol == "BENCH":
                raise RuntimeError("Failed to fetch kline for BENCH")
            return main_df

        monkeypatch.setattr(backtest, "fetch_kline", fake_fetch)
        monkeypatch.setattr(backtest, "load_strategy", lambda *a, **kw: dict(_MA_CROSS_STRATEGY, benchmark="BENCH"))

        result = backtest.run_backtest("unused.yaml", "600000", "2024-01-01", "2024-06-30", 1000000)

        assert result["benchmark"]["symbol"] == "BENCH"
        assert "error" in result["benchmark"]
        assert "metrics" in result

    @pytest.mark.parametrize("n_rows", [0, 1])
    def test_benchmark_insufficient_rows_degrades_to_error_block(self, monkeypatch, make_kline_data, n_rows):
        # len(df) < 2 has no first/last pair to diff — degrade to a clean error note.
        main_df = pd.DataFrame(make_kline_data(120, "volatile"))
        bm_df = pd.DataFrame(make_kline_data(n_rows, "up", base=4000.0)) if n_rows else pd.DataFrame()

        def fake_fetch(symbol, *a, **kw):
            return bm_df if symbol == "BENCH" else main_df

        monkeypatch.setattr(backtest, "fetch_kline", fake_fetch)
        monkeypatch.setattr(backtest, "load_strategy", lambda *a, **kw: dict(_MA_CROSS_STRATEGY, benchmark="BENCH"))

        result = backtest.run_backtest("unused.yaml", "600000", "2024-01-01", "2024-06-30", 1000000)

        assert result["benchmark"]["symbol"] == "BENCH"
        assert f"Insufficient benchmark data: {n_rows} rows" in result["benchmark"]["error"]
        assert "metrics" in result  # the main result is unaffected

    def test_benchmark_symbol_survives_parameter_substitution(self, tmp_path):
        # _resolve_refs would otherwise turn the numeric-looking symbol "000300" into int 300.
        yaml_path = tmp_path / "strat.yaml"
        yaml_path.write_text(
            'name: bm\nparameters:\n  thr: 0.5\nbenchmark: "000300"\n'
            'entry:\n  conditions:\n    - indicator: rsi\n      operator: "<"\n      value: "{thr}"\n',
            encoding="utf-8",
        )
        strat = backtest.load_strategy(str(yaml_path))
        assert strat["benchmark"] == "000300"
        assert strat["entry"]["conditions"][0]["value"] == 0.5


class TestNextOpenExecution:
    def test_signal_waits_for_next_open_and_reports_signal_date(self):
        df = _df_from_closes([10, 11, 12, 13])
        df["open"] = [10, 10, 20, 25]
        result = backtest.simulate(
            df,
            {
                "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 10}]},
                "simulation": {"commission": 0, "slippage": 0},
            },
            1000,
            "AAPL",
        )
        buy = result["trades"][0]
        assert buy["date"] == "2024-01-03"
        assert buy["signal_date"] == "2024-01-02"
        assert buy["price"] == 20
        assert buy["shares"] == 50
        assert result["execution"]["model"] == "next_open"

    def test_last_bar_signal_cannot_trade_in_the_past(self):
        strategy = {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 15}]}}
        assert backtest.simulate(_df_from_closes([10, 10, 20]), strategy, 1000, "AAPL")["trades"] == []

    def test_zero_volume_defers_order_and_keeps_equity_dates(self):
        df = _df_from_closes([10, 11, 12, 13])
        df.loc[2, "volume"] = 0
        sim = backtest.simulate(
            df, {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 10}]}}, 1000, "AAPL"
        )
        assert sim["trades"][0]["date"] == "2024-01-04"
        assert sim["trades"][0]["signal_date"] == "2024-01-02"
        assert len(sim["equity_curve"]) == 3

    def test_pending_entry_is_cancelled_when_latest_entry_condition_fails(self):
        df = _df_from_closes([10, 11, 5, 5])
        df.loc[2, "volume"] = 0
        sim = backtest.simulate(
            df, {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 10}]}}, 1000, "AAPL"
        )
        assert sim["trades"] == []
        assert sim["execution"]["blocked_orders"] == 1
        assert sim["execution"]["unfilled_order"] is False

    def test_explicit_locked_limit_blocks_buy(self):
        df = _df_from_closes([9, 10.5, 11, 12])
        df.loc[2, "limit_up"] = 11
        sim = backtest.simulate(
            df, {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 10}]}}, 10000, "600519"
        )
        assert sim["trades"][0]["date"] == "2024-01-04"
        assert sim["execution"]["blocked_orders"] == 1
        assert len(sim["equity_curve"]) == 3

    def test_next_open_requires_valid_open(self):
        df = _df_from_closes([10, 10, 10])
        df.loc[2, "open"] = float("nan")
        with pytest.raises(ValueError, match="open"):
            backtest.simulate(
                df, {"entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 10}]}}, 1000, "AAPL"
            )


def test_next_open_rejects_duplicate_dates():
    df = _df_from_closes([10, 10, 12])
    df.loc[2, "date"] = df.loc[1, "date"]
    with pytest.raises(ValueError, match="dates"):
        backtest.simulate(df, {"entry": _ALWAYS_ENTRY}, 10000, "600519")


def test_blocked_exit_keeps_original_signal_and_fills_at_later_open():
    df = _df_from_closes([10, 10, 12, 10, 9])
    df.loc[2, "open"] = 10
    df.loc[3, "limit_down"] = 10
    strategy = {"entry": _ALWAYS_ENTRY, "exit": {"take_profit": 0.2}, "simulation": {"slippage": 0, "commission": 0}}
    result = backtest.simulate(df, strategy, 10000, "600519")
    sell = next(trade for trade in result["trades"] if trade["type"] == "sell")
    assert sell["signal_date"] == "2024-01-03"
    assert sell["date"] == "2024-01-05"
    assert sell["price"] == 9
    assert sell["pnl"] == -1000
    assert result["execution"]["blocked_orders"] == 1


def test_next_open_trailing_peak_uses_held_closes_after_gap_entry():
    df = _df_from_closes([10, 11, 12, 13])
    df.loc[2, ["open", "high"]] = 20
    strategy = {
        "entry": {"conditions": [{"indicator": "close", "operator": ">", "value": 10}]},
        "exit": {"stop_loss": -0.9, "take_profit": 10, "trailing_stop": 0.1},
        "simulation": {"slippage": 0, "commission": 0},
    }
    result = backtest.simulate(df, strategy, 1000, "AAPL")
    assert result["trades"][0]["price"] == 20
    assert [trade for trade in result["trades"] if trade["type"] == "sell"] == []


def test_first_close_signal_executes_at_second_day_open():
    result = backtest.simulate(
        _df_from_closes([10, 11, 12]),
        {"entry": _ALWAYS_ENTRY, "simulation": {"commission": 0, "slippage": 0}},
        1000,
        "AAPL",
    )
    assert result["trades"][0]["signal_date"] == "2024-01-01"
    assert result["trades"][0]["date"] == "2024-01-02"
    assert result["trades"][0]["price"] == 11


def test_cross_condition_does_not_read_last_bar_as_previous_first_bar():
    assert backtest.check_condition(pd.Series([10, 9]), "cross_above", 9.5, 0) is False
    assert backtest.check_condition(pd.Series([10, 9]), ">", 9.5, 0) is True


def test_simulation_outputs_match_preoptimization_baseline(make_kline_data):
    import hashlib

    # Complete outputs captured from 41bf904 before the indexing optimization.
    results = []
    for execution in ("next_open", "same_close"):
        for trend in ("up", "down", "volatile", "flat"):
            frame = pd.DataFrame(make_kline_data(250, trend))
            strategy = {
                "entry": {"conditions": [{"indicator": "rsi", "period": 14, "operator": "<", "value": 65}]},
                "exit": {
                    "conditions": [{"indicator": "rsi", "period": 14, "operator": ">", "value": 75}],
                    "stop_loss": -0.1,
                    "take_profit": 0.2,
                    "trailing_stop": 0.08,
                },
                "simulation": {"execution": execution, "commission": 0.0015, "stamp_tax": 0.001, "slippage": 0.001},
            }
            results.append(backtest.simulate(frame, strategy, 100000, "600519"))
            gate = pd.Series([i % 3 != 0 for i in range(len(frame))])
            frame["limit_up"], frame["limit_down"] = frame["high"], frame["low"]
            frame.loc[::7, "low"] = frame.loc[::7, "high"]
            frame.loc[::9, "volume"] = 0
            results.append(backtest.simulate(frame, strategy, 100000, "600519", entry_gate=gate, start="2024-03-01"))
    serialized = json.dumps(results, sort_keys=True, ensure_ascii=False, allow_nan=False)
    assert (
        hashlib.sha256(serialized.encode()).hexdigest()
        == "65d227da8d560bd7ff9ec5452207419872057fc8908fbee43e40bab8b3713476"
    )
