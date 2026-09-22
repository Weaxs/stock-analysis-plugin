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


class TestComputeRsi:
    def test_uptrend_high_rsi(self):
        close = pd.Series([10 + i * 0.5 for i in range(50)])
        rsi = compute_rsi(close, 14)
        assert float(rsi.iloc[-1]) >= 50

    def test_downtrend_low_rsi(self):
        close = pd.Series([30 - i * 0.5 for i in range(30)])
        rsi = compute_rsi(close, 14)
        assert float(rsi.iloc[-1]) < 50


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
        assert check_condition(s, ">", 5, 0) is False
        assert check_condition(s, ">", 5, 10) is False

    def test_equals(self):
        s = pd.Series([10, 20, 30])
        assert check_condition(s, "==", 20, 1) is True


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


class TestDiagnose:
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

    def test_all_zero_pnl_produces_inf_then_sanitized(self):
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
        assert metrics["profit_loss_ratio"] == float("inf")
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
