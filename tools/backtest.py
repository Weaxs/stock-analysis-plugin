#!/usr/bin/env python3
"""AlphaEvo — strategy backtesting engine with YAML DSL."""

import argparse
import contextlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _subproc import error_message, json_safe, run_tool, utf8_stdio
from stock_data import detect_market
from technical import (
    bollinger_series,
    ema_series,
    ma_series,
    macd_series,
    monthly_market_entry_series,
    monthly_momentum_series,
    rsi_series,
    trend_score_series,
)

# --------------- Strategy Loading ---------------


def load_strategy(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        strat = yaml.safe_load(f)
    params = strat.get("parameters", {})
    _resolve_refs(strat, params)
    return strat


def _resolve_refs(obj, params: dict):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "benchmark":
                continue  # benchmark is a symbol string ("000300"), never a {param} reference
            if isinstance(v, str):
                obj[k] = _substitute(v, params)
            else:
                _resolve_refs(v, params)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = _substitute(v, params)
            else:
                _resolve_refs(v, params)


def _substitute(s: str, params: dict):
    def _repl(m):
        key = m.group(1)
        val = params.get(key, m.group(0))
        return str(val)

    result = re.sub(r"\{(\w+)\}", _repl, s)
    try:
        return float(result) if "." in result else int(result)
    except (ValueError, TypeError):
        return result


# --------------- Data Fetching ---------------


def fetch_kline(
    symbol: str, start: str | None, end: str | None, include_warmup: bool = False, warmup_bars: int = 0
) -> pd.DataFrame:
    count = 500
    if start and end:
        d0 = datetime.strptime(start, "%Y-%m-%d")
        d1 = datetime.strptime(end, "%Y-%m-%d")
        count = max((datetime.now() - d0).days + 60, (d1 - d0).days + 60, 300)
    count += warmup_bars

    data = run_tool(
        "stock_data.py", ["kline", symbol, "--period", "daily", "--count", str(count)], preserve_errors=True
    )
    if data is None:
        raise RuntimeError(f"Failed to fetch kline for {symbol}")
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(data["error"])

    df = pd.DataFrame(data)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)

    if start and not include_warmup:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return df.reset_index(drop=True)


# --------------- Indicators ---------------
#
# Series math lives in technical.py (ma_series/ema_series/macd_series/rsi_series/
# bollinger_series); warmup remains NaN so it cannot create trading conditions.


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    return rsi_series(close, period)


# Pure aliases (identical signatures); the other wrappers below pick one leg of the MACD tuple.
compute_ma = ma_series
compute_ema = ema_series


def compute_macd_dif(close: pd.Series) -> pd.Series:
    return macd_series(close)[0]


def compute_macd_dea(close: pd.Series) -> pd.Series:
    return macd_series(close)[1]


def compute_macd_hist(close: pd.Series) -> pd.Series:
    return macd_series(close)[2]


def compute_volume_ratio(volume: pd.Series, period: int = 5) -> pd.Series:
    previous_average = ma_series(volume, period).shift(1)
    return volume / previous_average.replace(0, np.nan)


def compute_price_change(close: pd.Series) -> pd.Series:
    return close.pct_change() * 100


def compute_bollinger_position(close: pd.Series, period: int = 20) -> pd.Series:
    upper, mid, lower = bollinger_series(close, period)
    width = (upper - lower).replace(0, np.nan)
    return (close - lower) / width


def _resolve_ma_pair(fast: int | None, slow: int | None, period: int | None, default: tuple) -> tuple:
    """Explicit fast/slow periods win; a legacy encoded `period` (510 = MA5-MA10,
    500 = MA5-MA20 via the slow==0 → fast*4 fallback) still decodes for old YAMLs."""
    if fast is None and slow is None and period and period >= 100:
        fast, slow = period // 100, period % 100
        if slow == 0:
            slow = fast * 4
        return fast, slow
    return fast or default[0], slow or default[1]


def compute_ma_diff(
    close: pd.Series, period: int | None = None, fast: int | None = None, slow: int | None = None
) -> pd.Series:
    """Difference between two MAs. Prefer explicit fast/slow; `period` keeps the legacy encoding."""
    fast, slow = _resolve_ma_pair(fast, slow, period, (5, 20))
    return compute_ma(close, fast) - compute_ma(close, slow)


def compute_ema_diff(
    close: pd.Series, period: int | None = None, fast: int | None = None, slow: int | None = None
) -> pd.Series:
    fast, slow = _resolve_ma_pair(fast, slow, period, (12, 26))
    return compute_ema(close, fast) - compute_ema(close, slow)


CONDITION_OPERATORS = {">", "<", ">=", "<=", "==", "cross_above", "cross_below"}
DEFAULT_INDICATOR_PERIODS = {
    "rsi": 14,
    "ma": 20,
    "ema": 20,
    "monthly_momentum": 63,
    "volume_ratio": 5,
    "bollinger_position": 20,
}
DEFAULT_TREND_PERIODS = (20, 60, 14)


def get_indicator(
    df: pd.DataFrame, name: str, period: int | None = None, fast: int | None = None, slow: int | None = None
) -> pd.Series:
    close = df["close"]
    dispatch = {
        "rsi": lambda: compute_rsi(close, period or DEFAULT_INDICATOR_PERIODS["rsi"]),
        "ma": lambda: compute_ma(close, period or DEFAULT_INDICATOR_PERIODS["ma"]),
        "ema": lambda: compute_ema(close, period or DEFAULT_INDICATOR_PERIODS["ema"]),
        "macd_dif": lambda: compute_macd_dif(close),
        "macd_dea": lambda: compute_macd_dea(close),
        "macd": lambda: compute_macd_hist(close),
        "ma_diff": lambda: compute_ma_diff(close, period, fast, slow),
        "ema_diff": lambda: compute_ema_diff(close, period, fast, slow),
        "trend_score": lambda: trend_score_series(
            df,
            fast if fast is not None else DEFAULT_TREND_PERIODS[0],
            slow if slow is not None else DEFAULT_TREND_PERIODS[1],
            period if period is not None else DEFAULT_TREND_PERIODS[2],
        ),
        "monthly_momentum": lambda: monthly_momentum_series(
            df, period or DEFAULT_INDICATOR_PERIODS["monthly_momentum"]
        ),
        "volume_ratio": lambda: compute_volume_ratio(df["volume"], period or DEFAULT_INDICATOR_PERIODS["volume_ratio"]),
        "price_change": lambda: compute_price_change(close),
        "bollinger_position": lambda: compute_bollinger_position(
            close, period or DEFAULT_INDICATOR_PERIODS["bollinger_position"]
        ),
        "close": lambda: close,
        "volume": lambda: df["volume"].astype(float),
    }
    fn = dispatch.get(name.lower())
    if fn is None:
        raise ValueError(f"Unknown indicator: {name}")
    return fn()


# --------------- Condition Evaluation ---------------


def check_condition(series: pd.Series | np.ndarray, operator: str, value: float, idx: int) -> bool:
    if idx < 0 or idx >= len(series):
        return False
    values = series.iloc if isinstance(series, pd.Series) else series
    cur = float(values[idx])
    ops = {
        ">": cur > value,
        "<": cur < value,
        ">=": cur >= value,
        "<=": cur <= value,
        "==": abs(cur - value) < 1e-6,
    }
    if operator in ops:
        return ops[operator]
    if idx == 0:
        return False
    prev = float(values[idx - 1])
    if operator == "cross_above":
        return prev <= value and cur > value
    if operator == "cross_below":
        return prev >= value and cur < value
    return False


def _condition_parameters(cond: dict) -> tuple:
    values = []
    for key in ("period", "fast_period", "slow_period"):
        if key not in cond:
            values.append(None)
            continue
        value = cond[key]
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} must be a positive integer or integer string, got {value!r}")
        values.append(value)
    return tuple(values)


def _condition_value(cond: dict) -> float:
    raw = cond.get("value")
    if isinstance(raw, bool):
        raise ValueError(f"condition value must be a finite number, got {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as e:
        raise ValueError(f"condition value must be a finite number, got {raw!r}") from e
    if not math.isfinite(value):
        raise ValueError(f"condition value must be a finite number, got {raw!r}")
    return value


def _strategy_warmup_bars(strategy: dict) -> int:
    lookback = 0
    for side in ("entry", "exit"):
        for cond in strategy.get(side, {}).get("conditions", []):
            name = cond["indicator"].lower()
            period, fast, slow = _condition_parameters(cond)
            if name in ("ma_diff", "ema_diff"):
                defaults = (5, 20) if name == "ma_diff" else (12, 26)
                fast, slow = _resolve_ma_pair(fast, slow, period, defaults)
                bars = max(fast, slow)
            elif name == "trend_score":
                bars = max(slow or DEFAULT_TREND_PERIODS[1], (period or DEFAULT_TREND_PERIODS[2]) + 1)
            elif name in ("macd_dif", "macd_dea", "macd"):
                bars = 26 if name == "macd_dif" else 34
            elif name == "price_change":
                bars = 2
            else:
                bars = period or DEFAULT_INDICATOR_PERIODS.get(name, 1)
                if name in ("rsi", "monthly_momentum", "volume_ratio"):
                    bars += 1
            lookback = max(lookback, bars + (cond.get("operator") in ("cross_above", "cross_below")))
    return lookback


def _condition_series(df: pd.DataFrame, cond: dict, cache: dict) -> tuple[np.ndarray, str]:
    if cond.get("operator") not in CONDITION_OPERATORS:
        raise ValueError(f"Unknown condition operator: {cond.get('operator')}")
    ind_name = cond["indicator"]
    period, fast, slow = _condition_parameters(cond)
    cache_key = f"{ind_name}_{period}_{fast}_{slow}"
    if cache_key not in cache:
        cache[cache_key] = get_indicator(df, ind_name, period, fast, slow).to_numpy()
    label = f"{ind_name}({fast},{slow})" if fast or slow else f"{ind_name}({period})" if period else ind_name
    return cache[cache_key], label


def evaluate_conditions(df: pd.DataFrame, conditions: list, logic: str, idx: int, cache: dict) -> tuple[bool, str]:
    results = []
    reasons = []
    for cond in conditions:
        series, label = _condition_series(df, cond, cache)
        op = cond["operator"]
        val = _condition_value(cond)
        hit = check_condition(series, op, val, idx)
        results.append(hit)
        if hit:
            cur_val = round(float(series[idx]), 2)
            reasons.append(f"{label} = {cur_val} {op} {val}")

    if logic == "all":
        return all(results), "; ".join(reasons)
    return any(results), "; ".join(reasons)


def _entry_warmup_warnings(df: pd.DataFrame, strategy: dict, start: str, execution: str) -> list[str]:
    dates = pd.to_datetime(df["date"], errors="coerce")
    window = np.flatnonzero((dates >= start).to_numpy())
    first_bar = max(1, int(window[0]))
    signal_idx = first_bar if execution == "same_close" else first_bar - 1
    warnings = []
    cache = {}
    for cond in strategy.get("entry", {}).get("conditions", []):
        series, label = _condition_series(df, cond, cache)
        indices = [signal_idx]
        if cond["operator"] in ("cross_above", "cross_below"):
            indices.append(signal_idx - 1)
        if any(idx < 0 or pd.isna(series[idx]) for idx in indices):
            signal_date = str(pd.Timestamp(df["date"].iloc[signal_idx]).date())
            warnings.append(
                f"Entry indicator is unavailable: {label} at first window signal {signal_date}; check warmup and data quality"
            )
    return warnings


# --------------- Simulation ---------------


def _validate_adjustment_basis(df: pd.DataFrame) -> None:
    if "adjusted" not in df:
        return
    bases = set(df["adjusted"].fillna("unknown").astype(str))
    if not bases or not bases <= {"qfq", "total_return", "none"}:
        raise ValueError("adjusted contains unknown adjustment basis; expected qfq, total_return or none")
    if len(bases) != 1:
        raise ValueError("adjusted must use a single adjustment basis: qfq, total_return or none")


def simulate(
    df: pd.DataFrame,
    strategy: dict,
    capital: float,
    symbol: str,
    *,
    entry_gate: pd.Series | None = None,
    start: str | None = None,
) -> dict:
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be finite and positive")
    _validate_adjustment_basis(df)
    if not np.isfinite(df["close"]).all() or (df["close"] <= 0).any():
        raise ValueError("backtest prices must be finite and positive")
    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("backtest dates must be valid, unique and increasing")
    first_bar = 1
    if start is not None:
        start_date = pd.Timestamp(start)
        window_bars = np.flatnonzero((dates >= start_date).to_numpy())
        if pd.isna(start_date) or not len(window_bars):
            raise ValueError("start must be a valid date within the backtest data")
        first_bar = max(1, int(window_bars[0]))
    if entry_gate is not None:
        if not entry_gate.index.equals(df.index) or not (entry_gate.isna() | entry_gate.isin([True, False])).all():
            raise ValueError("entry_gate must contain aligned booleans or unavailable values")
        entry_gate = entry_gate.fillna(False).astype(bool).to_numpy()
    market = detect_market(symbol)
    lot_size = 100 if market == "A" else 1
    sim_cfg = strategy.get("simulation", {})
    execution = sim_cfg.get("execution", "next_open")
    if execution not in ("next_open", "same_close"):
        raise ValueError("simulation.execution must be next_open or same_close")
    if execution == "next_open" and ("open" not in df or not np.isfinite(df["open"]).all() or (df["open"] <= 0).any()):
        raise ValueError("next_open requires finite positive open prices")
    slippage = float(sim_cfg.get("slippage", 0.001))
    commission = float(sim_cfg.get("commission", 0.0015))
    tax_raw = sim_cfg.get("stamp_tax")  # sell-side only (e.g. A股印花税); absent/empty = 0 (disabled)
    stamp_tax = float(0.0 if tax_raw is None else tax_raw)
    for name, rate in (("slippage", slippage), ("commission", commission), ("stamp_tax", stamp_tax)):
        if not 0 <= rate < 1:
            raise ValueError(f"simulation.{name} must be a rate in [0, 1), got {rate}")
    if commission + stamp_tax >= 1:
        raise ValueError(f"simulation commission + stamp_tax must be < 1, got {commission + stamp_tax}")

    entry = strategy.get("entry", {})
    exit_ = strategy.get("exit", {})
    position_cfg = strategy.get("position", {})
    size_frac = float(position_cfg.get("size", 1.0))
    if not 0 <= size_frac <= 1:
        raise ValueError("position.size must be a fraction in [0, 1]")
    if position_cfg.get("max_positions", 1) != 1:
        raise ValueError("position.max_positions must be 1 for the single-position engine")
    stop_loss = float(exit_.get("stop_loss", -0.10))
    take_profit = float(exit_.get("take_profit", 0.50))
    ts_val = exit_.get("trailing_stop")
    trailing_stop = float(ts_val) if ts_val is not None else None
    if not math.isfinite(stop_loss) or stop_loss >= 0:
        raise ValueError("exit.stop_loss must be a finite negative fraction")
    if not math.isfinite(take_profit) or take_profit <= 0:
        raise ValueError("exit.take_profit must be a finite positive fraction")
    if trailing_stop is not None and (not math.isfinite(trailing_stop) or not 0 < trailing_stop < 1):
        raise ValueError("exit.trailing_stop must be a finite fraction in (0, 1)")

    entry_conditions = entry.get("conditions", [])
    entry_logic = entry.get("logic", "all")
    exit_conditions = exit_.get("conditions", [])
    exit_logic = exit_.get("logic", "any")
    for side, logic in (("entry", entry_logic), ("exit", exit_logic)):
        if logic not in ("all", "any"):
            raise ValueError(f"{side}.logic must be all or any")

    cache: dict = {}
    for cond in entry_conditions + exit_conditions:
        _condition_series(df, cond, cache)
        _condition_value(cond)
    trades = []
    equity_curve = []
    holding = None
    cash = capital
    trade_id = 0
    commission_paid = 0.0
    stamp_tax_paid = 0.0
    pending = None
    blocked_orders = 0

    def tradable(row, side):
        if "volume" in row and (not pd.notna(row["volume"]) or row["volume"] <= 0):
            return False
        limit = row.get("limit_up" if side == "buy" else "limit_down")
        # shortcut: limits need supplied historical prices; add verified limit series before enforcing provider bars.
        return not (
            limit is not None
            and pd.notna(limit)
            and row.get("high") == row.get("low")
            and abs(float(row["high"]) - float(limit)) < 0.005
        )

    bars = df.to_dict("records")
    closes = df["close"].to_numpy()
    date_strings = [str(value.date()) if hasattr(value, "date") else str(value) for value in df["date"]]
    signal_dates = dates.dt.strftime("%Y-%m-%d").tolist()
    for i in range(first_bar, len(df)):
        row = bars[i]
        date_str = date_strings[i]
        signal_idx = i if execution == "same_close" else i - 1
        price = float(closes[signal_idx])
        fill_price = float(row["close"] if execution == "same_close" else row["open"])
        signal_date = signal_dates[signal_idx]

        if holding is None:
            hit, reason = evaluate_conditions(df, entry_conditions, entry_logic, signal_idx, cache)
            if pending:
                if hit:
                    reason, signal_date = pending
                else:
                    pending = None
            if entry_gate is not None and not entry_gate[signal_idx]:
                hit, pending = False, None
            if hit and not tradable(row, "buy"):
                pending = (reason, signal_date)
                blocked_orders += 1
            if hit and tradable(row, "buy"):
                pending = None
                buy_price = fill_price * (1 + slippage)
                buy_amount = cash * size_frac
                shares = int(buy_amount / (buy_price * (1 + commission)) / lot_size) * lot_size
                if shares <= 0:
                    equity_curve.append({"date": date_str, "equity": round(cash, 2)})
                    continue
                cost = shares * buy_price * (1 + commission)
                commission_paid += shares * buy_price * commission
                cash -= cost
                trade_id += 1
                holding = {
                    "id": trade_id,
                    "buy_date": date_str,
                    "buy_price": round(buy_price, 4),
                    "shares": shares,
                    "cost": cost,
                    "peak_close": float(row["close"]),
                }
                trades.append(
                    {
                        "id": trade_id,
                        "type": "buy",
                        "date": date_str,
                        "signal_date": signal_date,
                        "price": round(buy_price, 2),
                        "shares": shares,
                        "amount": round(cost, 2),
                        "reason": reason,
                    }
                )
        else:
            pnl_pct = (price - holding["buy_price"]) / holding["buy_price"]
            peak_close = max(holding["peak_close"], price)
            holding["peak_close"] = peak_close
            sell = False
            reason = ""

            if pnl_pct <= stop_loss:
                sell, reason = True, f"止损触发 ({round(pnl_pct * 100, 1)}% <= {round(stop_loss * 100, 1)}%)"
            elif trailing_stop is not None and price <= peak_close * (1 - trailing_stop):
                drawdown = (peak_close - price) / peak_close
                sell, reason = (
                    True,
                    f"移动止损触发 (较高点回撤 {round(drawdown * 100, 1)}% >= {round(trailing_stop * 100, 1)}%)",
                )
            elif pnl_pct >= take_profit:
                sell, reason = True, f"止盈触发 ({round(pnl_pct * 100, 1)}% >= {round(take_profit * 100, 1)}%)"
            elif exit_conditions:
                sell, reason = evaluate_conditions(df, exit_conditions, exit_logic, signal_idx, cache)

            if pending:
                sell, reason, signal_date = True, pending[0], pending[1]
            if sell and not tradable(row, "sell"):
                pending = (reason, signal_date)
                blocked_orders += 1
            if sell and tradable(row, "sell"):
                pending = None
                sell_price = fill_price * (1 - slippage)
                gross = holding["shares"] * sell_price
                proceeds = gross * (1 - commission - stamp_tax)
                commission_paid += gross * commission
                stamp_tax_paid += gross * stamp_tax
                cash += proceeds
                pnl = proceeds - holding["cost"]
                days = 0
                try:
                    d0 = datetime.strptime(holding["buy_date"], "%Y-%m-%d")
                    d1 = datetime.strptime(date_str, "%Y-%m-%d")
                    days = (d1 - d0).days
                except ValueError:
                    pass
                trade_id_sell = holding["id"]
                trades.append(
                    {
                        "id": trade_id_sell,
                        "type": "sell",
                        "date": date_str,
                        "signal_date": signal_date,
                        "price": round(sell_price, 2),
                        "shares": holding["shares"],
                        "amount": round(proceeds, 2),
                        "reason": reason,
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl / holding["cost"], 4),
                        "holding_days": days,
                    }
                )
                holding = None

        equity = cash
        if holding:
            equity += holding["shares"] * float(row["close"])
        equity_curve.append({"date": date_str, "equity": round(equity, 2)})

    if holding:
        last_price = float(closes[-1])
        equity = cash + holding["shares"] * last_price
        equity_curve[-1]["equity"] = round(equity, 2)

    return {
        "trades": trades,
        "equity_curve": equity_curve,
        "final_equity": equity_curve[-1]["equity"] if equity_curve else capital,
        "execution": {
            "model": execution,
            "blocked_orders": blocked_orders,
            "unfilled_order": pending is not None,
            "limitations": [
                "Stops are close signals, not intraday guaranteed stop prices",
                "Historical price limits are enforced only when limit_up/limit_down are supplied",
                "Queue priority, partial fills, market impact and settlement are not simulated",
                "Adjusted OHLC are synthetic prices; corporate actions and cash dividends are not simulated separately",
            ]
            + (
                ["same_close uses the signal day's close and can overstate executable returns"]
                if execution == "same_close"
                else []
            ),
        },
        "costs": {
            "slippage": slippage,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "commission_paid": round(commission_paid, 2),
            "stamp_tax_paid": round(stamp_tax_paid, 2),
        },
    }


def simulate_monthly_market(
    df: pd.DataFrame, market: pd.DataFrame, capital: float, symbol: str, start: str, allocation: float = 1.0
) -> dict:
    """Fixed research protocol: three drifting sleeves, market-gated entries and explicit warmup."""
    if isinstance(allocation, bool) or not math.isfinite(allocation) or not 0 < allocation <= 1:
        raise ValueError("allocation must be finite and in (0, 1]")
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be finite and positive")
    start_date = pd.Timestamp(start)
    dates = pd.to_datetime(df["date"], errors="coerce")
    before = dates[(dates < start_date) & (df["volume"] > 0)] if "volume" in df else dates[dates < start_date]
    if pd.isna(start_date) or before.empty or not (dates >= start_date).any():
        raise ValueError("start requires both prehistory and evaluation bars")
    sleeve_capital = math.floor(capital * allocation / 3 * 100) / 100
    curves, trades, paid = [], [], 0.0
    for horizon in (21, 63, 252):
        gate = monthly_market_entry_series(df, market, horizon, activation=before.iloc[-1])
        condition = {"indicator": "monthly_momentum", "period": horizon, "value": 0}
        strategy = {
            "entry": {"conditions": [{**condition, "operator": ">"}]},
            "exit": {"conditions": [{**condition, "operator": "<"}], "stop_loss": -1, "take_profit": 1e9},
            "simulation": {"execution": "next_open", "commission": 0.001, "slippage": 0.001, "stamp_tax": 0},
        }
        result = simulate(df, strategy, sleeve_capital, symbol, entry_gate=gate)
        curves.append(pd.Series({row["date"]: row["equity"] for row in result["equity_curve"]}))
        trades.extend({**trade, "horizon": horizon} for trade in result["trades"])
        paid += result["costs"]["commission_paid"]
    total = pd.concat(curves, axis=1).sum(axis=1) + capital - sleeve_capital * 3
    return {
        "model": "monthly_momentum_market_entry",
        "validation_status": "not_validated",
        "experimental": True,
        "allocation": allocation,
        "start": str(start_date.date()),
        "warmup_start": str(dates.iloc[0].date()),
        "trades": sorted(trades, key=lambda trade: (trade["date"], trade["horizon"])),
        "equity_curve": [{"date": date, "equity": round(float(value), 2)} for date, value in total.items()],
        "final_equity": round(float(total.iloc[-1]), 2),
        "costs": {"commission": 0.001, "slippage": 0.001, "stamp_tax": 0, "commission_paid": round(paid, 2)},
    }


_BIWEEKLY_ANCHOR = date(1970, 1, 5)  # Monday epoch for continuous 14-day buckets across year boundaries


def _schedule_bucket(ts: pd.Timestamp, frequency: str):
    if frequency == "weekly":
        iso = ts.isocalendar()
        return (int(iso.year), int(iso.week))
    if frequency == "biweekly":
        return (ts.date() - _BIWEEKLY_ANCHOR).days // 14
    if frequency == "monthly":
        return (ts.year, ts.month)
    if frequency == "quarterly":
        return (ts.year, (ts.month - 1) // 3)
    raise ValueError(f"Unsupported schedule frequency: {frequency}")


def _canonical_symbol(symbol: str) -> str:
    raw = str(symbol).strip()
    upper = raw.upper()
    # A-share explicit indices keep lowercase prefix (sh000xxx / sz399xxx)
    if re.fullmatch(r"(?:SH000|SZ399)\d{3}", upper):
        return upper.lower()
    # A-share stocks / ETFs: sh600519 and 600519 refer to the same instrument
    m_cn = re.fullmatch(r"(?:SH|SZ|BJ)(\d{6})", upper)
    if m_cn:
        return m_cn.group(1)
    # HK stocks / ETFs: 700.HK / 0700.HK / 00700.HK refer to the same instrument
    m_hk = re.fullmatch(r"0*(\d{1,5})\.HK", upper)
    if m_hk:
        return f"{int(m_hk.group(1)):05d}.HK"
    return upper


def _infer_currency(symbol: str, market: str, df: pd.DataFrame) -> str | None:
    if "currency" in df.columns:
        vals = {str(v).strip().upper() for v in df["currency"].dropna().tolist() if str(v).strip()}
        if len(vals) > 1:
            raise ValueError(f"Mixed currencies within dataset for {symbol}: {sorted(vals)}")
        if len(vals) == 1:
            return next(iter(vals))
    if market == "A":
        return "CNY"
    if market == "US":
        return "USD"
    if market == "JP":
        return "JPY"
    if market == "KR":
        return "KRW"
    if market == "TW":
        return "TWD"
    if market == "HK":
        # HKEX multi-counter conventions: 8xxxx.HK = RMB counter, 09xxx.HK (9000-9999) = USD ETF counter
        m = re.fullmatch(r"0*(\d{1,5})\.HK", _canonical_symbol(symbol))
        if m:
            code = int(m.group(1))
            if 80000 <= code <= 89999:
                return "CNY"
            if 9000 <= code <= 9999:
                return "USD"
            return "HKD"
    return None


def _parse_portfolio_config(strategy: dict, symbols: list[str], *, signal_mode: bool = False) -> dict:
    portfolio_cfg = strategy.get("portfolio") or {}
    pos_cfg = strategy.get("position") or {}
    dca_cfg = strategy.get("dca") or {}

    rebalance = str(portfolio_cfg.get("rebalance", "none")).lower()
    if rebalance not in ("none", "weekly", "monthly", "quarterly"):
        raise ValueError("portfolio.rebalance must be one of none, weekly, monthly, quarterly")
    if signal_mode and rebalance != "none":
        raise ValueError(
            "portfolio.rebalance is only supported in allocation mode (omit entry.conditions or set rebalance: none)"
        )

    weighting = str(portfolio_cfg.get("weighting", "equal")).lower()
    if weighting not in ("equal", "custom"):
        raise ValueError("portfolio.weighting must be equal or custom")

    if weighting == "custom":
        raw_weights = portfolio_cfg.get("weights")
        if not isinstance(raw_weights, dict) or not raw_weights:
            raise ValueError("portfolio.weights must be a non-empty mapping when weighting=custom")
        norm_weights: dict[str, float] = {}
        for raw_k, raw_v in raw_weights.items():
            key = _canonical_symbol(str(raw_k))
            if key in norm_weights:
                raise ValueError(f"Duplicate weight entry after symbol normalization: {raw_k!r}")
            w = float(raw_v)
            if not math.isfinite(w) or w < 0:
                raise ValueError(f"portfolio.weights[{raw_k}] must be a non-negative finite number")
            norm_weights[key] = w
        weights = {sym: norm_weights.get(_canonical_symbol(sym), 0.0) for sym in symbols}
        total_w = sum(weights.values())
        if not (0 < total_w <= 1.0001):
            raise ValueError(f"portfolio.weights sum must be in (0, 1], got {round(total_w, 4)}")
    else:
        w = 1.0 / len(symbols)
        weights = {sym: w for sym in symbols}

    drift = portfolio_cfg.get("drift_threshold")
    if drift is not None:
        drift = float(drift)
        if not math.isfinite(drift) or not 0 < drift < 1:
            raise ValueError("portfolio.drift_threshold must be in (0, 1)")

    size_frac = float(pos_cfg.get("size", 1.0))
    if not math.isfinite(size_frac) or not 0 <= size_frac <= 1:
        raise ValueError("position.size must be a fraction in [0, 1]")

    max_positions = portfolio_cfg.get("max_positions", pos_cfg.get("max_positions", len(symbols)))
    if isinstance(max_positions, bool) or not isinstance(max_positions, int) or max_positions <= 0:
        raise ValueError("max_positions must be a positive integer")

    dca = None
    if dca_cfg:
        freq = str(dca_cfg.get("frequency", "monthly")).lower()
        if freq not in ("weekly", "biweekly", "monthly", "quarterly"):
            raise ValueError("dca.frequency must be weekly, biweekly, monthly, or quarterly")
        amt = float(dca_cfg.get("amount", 0))
        if not math.isfinite(amt) or amt <= 0:
            raise ValueError("dca.amount must be a finite positive number")
        dca = {"frequency": freq, "amount": amt}

    return {
        "rebalance": rebalance,
        "weighting": weighting,
        "weights": weights,
        "drift_threshold": drift,
        "size": size_frac,
        "max_positions": max_positions,
        "dca": dca,
    }


def simulate_portfolio(
    datasets: dict[str, pd.DataFrame],
    strategy: dict,
    capital: float,
    *,
    start: str | None = None,
) -> dict:
    """Multi-asset portfolio simulation with optional periodic rebalancing, DCA, and multi-position signals."""
    if not datasets:
        raise ValueError("datasets must contain at least one symbol")
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be finite and positive")

    symbols = list(datasets.keys())
    seen_canon: dict[str, str] = {}
    for sym in symbols:
        canon = _canonical_symbol(sym)
        if canon in seen_canon:
            raise ValueError(f"Duplicate instrument in portfolio: {seen_canon[canon]!r} and {sym!r}")
        seen_canon[canon] = sym

    markets = {detect_market(sym) for sym in symbols}
    if len(markets) > 1:
        raise ValueError(f"Portfolio symbols must belong to a single market/currency, got {sorted(markets)}")
    market = next(iter(markets))
    currencies = {_infer_currency(sym, market, datasets[sym]) for sym in symbols}
    if len(currencies) > 1 or None in currencies:
        raise ValueError(
            f"Portfolio symbols must share a single verified quote currency, got {sorted(map(str, currencies))}"
        )
    currency = next(iter(currencies))
    lot_size = 100 if market == "A" else 1

    sim_cfg = strategy.get("simulation") or {}
    execution = sim_cfg.get("execution", "next_open")
    if execution not in ("next_open", "same_close"):
        raise ValueError("simulation.execution must be next_open or same_close")
    slippage = float(sim_cfg.get("slippage", 0.001))
    commission = float(sim_cfg.get("commission", 0.0015))
    tax_raw = sim_cfg.get("stamp_tax")
    stamp_tax = float(0.0 if tax_raw is None else tax_raw)
    for name, rate in (("slippage", slippage), ("commission", commission), ("stamp_tax", stamp_tax)):
        if not 0 <= rate < 1:
            raise ValueError(f"simulation.{name} must be a rate in [0, 1), got {rate}")
    if commission + stamp_tax >= 1:
        raise ValueError(f"simulation commission + stamp_tax must be < 1, got {commission + stamp_tax}")

    entry_cfg = strategy.get("entry") or {}
    exit_cfg = strategy.get("exit") or {}
    entry_conditions = entry_cfg.get("conditions") or []
    entry_logic = entry_cfg.get("logic", "all")
    exit_conditions = exit_cfg.get("conditions") or []
    exit_logic = exit_cfg.get("logic", "any")
    for side, logic in (("entry", entry_logic), ("exit", exit_logic)):
        if logic not in ("all", "any"):
            raise ValueError(f"{side}.logic must be all or any")

    signal_mode = bool(entry_conditions)
    cfg = _parse_portfolio_config(strategy, symbols, signal_mode=signal_mode)

    # Preserve single-symbol signal defaults (-0.10 / +0.50) in signal mode; disabled in pure allocation mode unless explicit
    if signal_mode:
        stop_loss = float(exit_cfg.get("stop_loss", -0.10))
        take_profit = float(exit_cfg.get("take_profit", 0.50))
    else:
        stop_loss = (
            float(exit_cfg["stop_loss"]) if "stop_loss" in exit_cfg and exit_cfg["stop_loss"] is not None else None
        )
        take_profit = (
            float(exit_cfg["take_profit"])
            if "take_profit" in exit_cfg and exit_cfg["take_profit"] is not None
            else None
        )
    ts_val = exit_cfg.get("trailing_stop")
    trailing_stop = float(ts_val) if ts_val is not None else None
    if stop_loss is not None and (not math.isfinite(stop_loss) or stop_loss >= 0):
        raise ValueError("exit.stop_loss must be a finite negative fraction")
    if take_profit is not None and (not math.isfinite(take_profit) or take_profit <= 0):
        raise ValueError("exit.take_profit must be a finite positive fraction")
    if trailing_stop is not None and (not math.isfinite(trailing_stop) or not 0 < trailing_stop < 1):
        raise ValueError("exit.trailing_stop must be a finite fraction in (0, 1)")

    # Validate each dataset and build date maps + indicator caches
    per_sym: dict[str, dict] = {}
    all_dates: set[pd.Timestamp] = set()
    for sym, raw_df in datasets.items():
        df = raw_df.copy().reset_index(drop=True)
        _validate_adjustment_basis(df)
        if not np.isfinite(df["close"]).all() or (df["close"] <= 0).any():
            raise ValueError(f"backtest prices for {sym} must be finite and positive")
        if execution == "next_open" and (
            "open" not in df or not np.isfinite(df["open"]).all() or (df["open"] <= 0).any()
        ):
            raise ValueError(f"next_open requires finite positive open prices for {sym}")
        dates = pd.to_datetime(df["date"], errors="coerce")
        if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
            raise ValueError(f"backtest dates for {sym} must be valid, unique and increasing")
        cache: dict = {}
        for cond in entry_conditions + exit_conditions:
            _condition_series(df, cond, cache)
            _condition_value(cond)
        date_to_idx = {ts: idx for idx, ts in enumerate(dates)}
        per_sym[sym] = {"df": df, "dates": dates, "bars": df.to_dict("records"), "idx": date_to_idx, "cache": cache}
        all_dates.update(dates.tolist())

    timeline = sorted(all_dates)
    if start is not None:
        start_ts = pd.Timestamp(start)
        eval_dates = [d for d in timeline if d >= start_ts]
    else:
        eval_dates = timeline[1:] if len(timeline) > 1 else timeline
    if not eval_dates:
        raise ValueError("start must be a valid date within the backtest data")

    cash = capital
    total_contributed = capital
    contributions = []
    holdings: dict[str, dict] = {}  # sym -> {shares, cost, buy_date, buy_price, peak_close}
    last_close: dict[str, float] = {}
    pending_exits: dict[str, tuple[str, str]] = {}  # sym -> (reason, signal_date)
    trades = []
    equity_curve = []
    twr_curve = []
    commission_paid = 0.0
    stamp_tax_paid = 0.0
    blocked_orders = 0
    trade_id = 0

    last_dca_bucket = None
    last_rebal_bucket = None
    target_symbols = [sym for sym in symbols if cfg["weights"].get(sym, 0.0) > 0]
    initially_allocated: set[str] = set()

    # Time-weighted return (TWR) tracks pure investment return strictly excluding external cash inflows
    twr_factor = 1.0
    prev_equity = capital

    def is_tradable(row: dict, side: str) -> bool:
        if "volume" in row and (not pd.notna(row["volume"]) or row["volume"] <= 0):
            return False
        limit = row.get("limit_up" if side == "buy" else "limit_down")
        return not (
            limit is not None
            and pd.notna(limit)
            and row.get("high") == row.get("low")
            and abs(float(row["high"]) - float(limit)) < 0.005
        )

    def fill_px(row: dict) -> float:
        return float(row["close"] if execution == "same_close" else row["open"])

    def sell_shares(sym: str, shares_to_sell: int, row: dict, date_str: str, reason: str):
        nonlocal cash, commission_paid, stamp_tax_paid, trade_id
        h = holdings.get(sym)
        if not h or shares_to_sell <= 0:
            return
        qty = min(shares_to_sell, h["shares"])
        if qty <= 0:
            return
        sell_price = fill_px(row) * (1 - slippage)
        gross = qty * sell_price
        proceeds = gross * (1 - commission - stamp_tax)
        commission_paid += gross * commission
        stamp_tax_paid += gross * stamp_tax
        cash += proceeds
        cost_portion = h["cost"] * (qty / h["shares"])
        pnl = proceeds - cost_portion
        days = 0
        with contextlib.suppress(ValueError):
            days = (datetime.strptime(date_str, "%Y-%m-%d") - datetime.strptime(h["buy_date"], "%Y-%m-%d")).days
        trade_id += 1
        trades.append(
            {
                "id": trade_id,
                "symbol": sym,
                "type": "sell",
                "date": date_str,
                "price": round(sell_price, 2),
                "shares": qty,
                "amount": round(proceeds, 2),
                "reason": reason,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / cost_portion, 4) if cost_portion > 0 else 0.0,
                "holding_days": days,
            }
        )
        h["shares"] -= qty
        h["cost"] -= cost_portion
        if h["shares"] <= 0:
            del holdings[sym]

    def buy_amount(sym: str, budget: float, row: dict, date_str: str, reason: str) -> bool:
        nonlocal cash, commission_paid, trade_id
        if budget <= 0 or cash <= 0:
            return False
        spend = min(budget, cash)
        buy_price = fill_px(row) * (1 + slippage)
        shares = int(spend / (buy_price * (1 + commission)) / lot_size) * lot_size
        if shares <= 0:
            return False
        cost = shares * buy_price * (1 + commission)
        commission_paid += shares * buy_price * commission
        cash -= cost
        trade_id += 1
        if sym in holdings:
            h = holdings[sym]
            new_shares = h["shares"] + shares
            h["cost"] += cost
            h["shares"] = new_shares
            h["buy_price"] = round(h["cost"] / new_shares, 4)
            h["peak_close"] = max(h["peak_close"], float(row["close"]))
        else:
            holdings[sym] = {
                "shares": shares,
                "cost": cost,
                "buy_date": date_str,
                "buy_price": round(buy_price, 4),
                "peak_close": float(row["close"]),
            }
        trades.append(
            {
                "id": trade_id,
                "symbol": sym,
                "type": "buy",
                "date": date_str,
                "price": round(buy_price, 2),
                "shares": shares,
                "amount": round(cost, 2),
                "reason": reason,
            }
        )
        return True

    for ts in eval_dates:
        date_str = str(ts.date())
        # Refresh last known closes for symbols that have a bar prior to ts
        for sym in symbols:
            info = per_sym[sym]
            idx = info["idx"].get(ts)
            if idx is not None and idx > 0 and sym not in last_close:
                last_close[sym] = float(info["bars"][idx - 1]["close"])

        # Determine valuation prices at contribution/execution time for exact TWR sub-period linking
        exec_prices: dict[str, float] = {}
        for sym in symbols:
            info = per_sym[sym]
            idx = info["idx"].get(ts)
            if idx is not None:
                exec_prices[sym] = fill_px(info["bars"][idx])
            elif sym in last_close:
                exec_prices[sym] = last_close[sym]

        # Step 1: Scheduled DCA contribution (links pre-deposit return before adding fresh cash)
        dca_added = 0.0
        if cfg["dca"] is not None:
            bucket = _schedule_bucket(ts, cfg["dca"]["frequency"])
            if last_dca_bucket is None:
                last_dca_bucket = bucket
            elif bucket != last_dca_bucket:
                last_dca_bucket = bucket
                dca_added = cfg["dca"]["amount"]

        pre_deposit_equity = None
        if dca_added > 0:
            pre_deposit_equity = cash + sum(
                h["shares"] * exec_prices.get(s, h["buy_price"]) for s, h in holdings.items()
            )
            if prev_equity > 0:
                twr_factor *= pre_deposit_equity / prev_equity
            cash += dca_added
            total_contributed += dca_added
            contributions.append({"date": date_str, "amount": round(dca_added, 2)})

        # Step 2: Execute either signal-driven multi-position mode or target-allocation/rebalance mode
        if signal_mode:
            sold_today: set[str] = set()
            # Exits first to free cash and slots; keep blocked risk exits pending across bars
            for sym in list(holdings.keys()):
                info = per_sym[sym]
                idx = info["idx"].get(ts)
                if idx is None:
                    continue
                sig_idx = idx if execution == "same_close" else idx - 1
                if sig_idx < 0:
                    continue
                row = info["bars"][idx]
                sig_price = float(info["bars"][sig_idx]["close"])
                sig_date = str(info["dates"].iloc[sig_idx].date())
                h = holdings[sym]
                h["peak_close"] = max(h["peak_close"], sig_price)
                pnl_pct = (sig_price - h["buy_price"]) / h["buy_price"]
                sell, reason = False, ""
                if stop_loss is not None and pnl_pct <= stop_loss:
                    sell, reason = True, f"止损触发 ({round(pnl_pct * 100, 1)}% <= {round(stop_loss * 100, 1)}%)"
                elif trailing_stop is not None and sig_price <= h["peak_close"] * (1 - trailing_stop):
                    dd = (h["peak_close"] - sig_price) / h["peak_close"]
                    sell, reason = (
                        True,
                        f"移动止损触发 (较高点回撤 {round(dd * 100, 1)}% >= {round(trailing_stop * 100, 1)}%)",
                    )
                elif take_profit is not None and pnl_pct >= take_profit:
                    sell, reason = True, f"止盈触发 ({round(pnl_pct * 100, 1)}% >= {round(take_profit * 100, 1)}%)"
                elif exit_conditions:
                    sell, reason = evaluate_conditions(info["df"], exit_conditions, exit_logic, sig_idx, info["cache"])

                if sym in pending_exits:
                    sell, reason, sig_date = True, pending_exits[sym][0], pending_exits[sym][1]

                if sell:
                    if not is_tradable(row, "sell"):
                        pending_exits[sym] = (reason, sig_date)
                        blocked_orders += 1
                    else:
                        pending_exits.pop(sym, None)
                        sell_shares(sym, h["shares"], row, date_str, reason)
                        sold_today.add(sym)

            # Entries for non-held symbols (excluding symbols sold on this same bar) up to max_positions
            open_slots = cfg["max_positions"] - len(holdings)
            if open_slots > 0 and cash > 0:
                candidates = []
                for sym in symbols:
                    if sym in holdings or sym in sold_today:
                        continue
                    info = per_sym[sym]
                    idx = info["idx"].get(ts)
                    if idx is None:
                        continue
                    sig_idx = idx if execution == "same_close" else idx - 1
                    if sig_idx < 0:
                        continue
                    hit, reason = evaluate_conditions(info["df"], entry_conditions, entry_logic, sig_idx, info["cache"])
                    if hit:
                        candidates.append((sym, info["bars"][idx], reason))
                for sym, row, reason in candidates[:open_slots]:
                    if not is_tradable(row, "buy"):
                        blocked_orders += 1
                        continue
                    remaining_slots = max(1, cfg["max_positions"] - len(holdings))
                    buy_amount(sym, cash * cfg["size"] / remaining_slots, row, date_str, reason)
        else:
            # Allocation / Rebalance / DCA mode
            rebal_due = False
            if cfg["rebalance"] != "none":
                bucket = _schedule_bucket(ts, cfg["rebalance"])
                if last_rebal_bucket is None:
                    last_rebal_bucket = bucket
                elif bucket != last_rebal_bucket:
                    last_rebal_bucket = bucket
                    rebal_due = True

            port_value = cash + sum(h["shares"] * exec_prices.get(s, h["buy_price"]) for s, h in holdings.items())
            rebalanced_today = False

            # First: complete initial allocation for any target-weight symbol not yet deployed (handles staggered start dates)
            pending_initial = [sym for sym in target_symbols if sym not in initially_allocated]
            if pending_initial and not rebal_due:
                unalloc_weight = sum(cfg["weights"][s] for s in pending_initial)
                avail_cash_for_init = cash - dca_added if dca_added > 0 else cash
                for sym in pending_initial:
                    info = per_sym[sym]
                    idx = info["idx"].get(ts)
                    if idx is None:
                        continue
                    row = info["bars"][idx]
                    if not is_tradable(row, "buy"):
                        blocked_orders += 1
                        continue
                    share_of_unalloc = cfg["weights"][sym] / unalloc_weight if unalloc_weight > 0 else 0.0
                    budget = min(avail_cash_for_init * share_of_unalloc, port_value * cfg["weights"][sym])
                    if buy_amount(sym, budget, row, date_str, "初始建仓"):
                        initially_allocated.add(sym)
                        avail_cash_for_init = max(0.0, avail_cash_for_init - budget)
                        unalloc_weight = max(0.0, unalloc_weight - cfg["weights"][sym])
                # Recompute port_value after initial buys
                port_value = cash + sum(h["shares"] * exec_prices.get(s, h["buy_price"]) for s, h in holdings.items())

            if rebal_due and port_value > 0:
                if cfg["drift_threshold"] is None:
                    do_rebalance = True
                else:
                    max_drift = max(
                        abs(
                            (holdings.get(s, {}).get("shares", 0) * exec_prices.get(s, 0.0) / port_value)
                            - cfg["weights"][s]
                        )
                        for s in symbols
                    )
                    do_rebalance = max_drift >= cfg["drift_threshold"]

                if do_rebalance:
                    reason = f"定期再平衡 ({cfg['rebalance']})"
                    # Sell overweight positions first
                    for sym in symbols:
                        info = per_sym[sym]
                        idx = info["idx"].get(ts)
                        if idx is None or sym not in holdings:
                            continue
                        row = info["bars"][idx]
                        px = exec_prices[sym]
                        target_val = port_value * cfg["weights"][sym]
                        cur_val = holdings[sym]["shares"] * px
                        if cur_val > target_val:
                            excess_shares = int((cur_val - target_val) / px / lot_size) * lot_size
                            if excess_shares > 0:
                                if not is_tradable(row, "sell"):
                                    blocked_orders += 1
                                else:
                                    sell_shares(sym, excess_shares, row, date_str, reason)
                    # Buy underweight positions
                    for sym in symbols:
                        info = per_sym[sym]
                        idx = info["idx"].get(ts)
                        if idx is None:
                            continue
                        row = info["bars"][idx]
                        px = exec_prices[sym]
                        target_val = port_value * cfg["weights"][sym]
                        cur_val = holdings.get(sym, {}).get("shares", 0) * px
                        deficit = target_val - cur_val
                        if deficit > 0:
                            if not is_tradable(row, "buy"):
                                blocked_orders += 1
                            elif buy_amount(sym, deficit, row, date_str, reason):
                                initially_allocated.add(sym)
                    rebalanced_today = True

            # Deploy fresh DCA cash whenever rebalance did not already deploy it
            if dca_added > 0 and not rebalanced_today:
                for sym in target_symbols:
                    info = per_sym[sym]
                    idx = info["idx"].get(ts)
                    if idx is None:
                        continue
                    row = info["bars"][idx]
                    if not is_tradable(row, "buy"):
                        blocked_orders += 1
                        continue
                    buy_amount(
                        sym, dca_added * cfg["weights"][sym], row, date_str, f"定期定投 ({cfg['dca']['frequency']})"
                    )

        # Step 3: Mark-to-market at close & link TWR sub-period
        for sym in symbols:
            info = per_sym[sym]
            idx = info["idx"].get(ts)
            if idx is not None:
                last_close[sym] = float(info["bars"][idx]["close"])
        equity = cash + sum(h["shares"] * last_close.get(s, h["buy_price"]) for s, h in holdings.items())
        if dca_added > 0 and pre_deposit_equity is not None:
            post_deposit_base = pre_deposit_equity + dca_added
            if post_deposit_base > 0:
                twr_factor *= equity / post_deposit_base
        elif prev_equity > 0:
            twr_factor *= equity / prev_equity
        prev_equity = equity
        equity_curve.append({"date": date_str, "equity": round(equity, 2)})
        twr_curve.append({"date": date_str, "equity": round(capital * twr_factor, 2)})

    final_equity = equity_curve[-1]["equity"] if equity_curve else capital
    final_holdings = []
    for sym, h in holdings.items():
        px = last_close.get(sym, h["buy_price"])
        mv = round(h["shares"] * px, 2)
        final_holdings.append(
            {
                "symbol": sym,
                "shares": h["shares"],
                "close": round(px, 4),
                "market_value": mv,
                "weight": round(mv / final_equity, 4) if final_equity > 0 else 0.0,
            }
        )

    out = {
        "mode": "portfolio_signal" if signal_mode else "portfolio_allocation",
        "symbols": symbols,
        "market": market,
        "currency": currency,
        "trades": trades,
        "equity_curve": equity_curve,
        "twr_curve": twr_curve,
        "final_equity": final_equity,
        "final_cash": round(cash, 2),
        "final_holdings": final_holdings,
        "total_contributed": round(total_contributed, 2),
        "time_weighted_return": round(twr_factor - 1.0, 4),
        "contributions": {
            "count": len(contributions),
            "total_added": round(total_contributed - capital, 2),
            "schedule": contributions,
        }
        if cfg["dca"] is not None
        else None,
        "portfolio": {
            "rebalance": cfg["rebalance"],
            "weighting": cfg["weighting"],
            "weights": {k: round(v, 4) for k, v in cfg["weights"].items()},
            "max_positions": cfg["max_positions"],
        },
        "execution": {
            "model": execution,
            "blocked_orders": blocked_orders,
            "unfilled_order": bool(pending_exits),
            "limitations": [
                "Stops are close signals, not intraday guaranteed stop prices",
                "Historical price limits are enforced only when limit_up/limit_down are supplied",
                "Queue priority, partial fills, market impact and settlement are not simulated",
                "Adjusted OHLC are synthetic prices; corporate actions and cash dividends are not simulated separately",
            ],
        },
        "costs": {
            "slippage": slippage,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "commission_paid": round(commission_paid, 2),
            "stamp_tax_paid": round(stamp_tax_paid, 2),
        },
    }
    return out


# --------------- Metrics ---------------


def compute_metrics(
    trades: list,
    equity_curve: list,
    initial: float,
    final: float,
    start: str,
    end: str,
    *,
    risk_curve: list | None = None,
    risk_initial: float | None = None,
    annual_return_basis: float | None = None,
) -> dict:
    sell_trades = [t for t in trades if t["type"] == "sell"]
    total_trades = len(sell_trades)
    winning = [t for t in sell_trades if t.get("pnl", 0) > 0]
    losing = [t for t in sell_trades if t.get("pnl", 0) <= 0]

    total_return = (final - initial) / initial if initial else 0
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d")
        d1 = datetime.strptime(end, "%Y-%m-%d")
        years = (d1 - d0).days / 365.25
    except (ValueError, TypeError):
        years = 1
    ann_source = annual_return_basis if annual_return_basis is not None else total_return
    annual_return = (1 + ann_source) ** (1 / max(years, 0.01)) - 1 if ann_source > -1 else ann_source

    curve_for_risk = risk_curve if risk_curve is not None else equity_curve
    base_for_risk = risk_initial if risk_initial is not None else initial
    equities = pd.Series([base_for_risk, *[e["equity"] for e in curve_for_risk]], dtype=float)
    peak = equities.cummax()
    dd = (equities - peak) / peak
    max_drawdown = float(dd.min()) if len(dd) > 0 else 0

    daily_returns = equities.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() - 0.03 / 252) / daily_returns.std() * math.sqrt(252)
    else:
        sharpe = 0

    win_rate = len(winning) / total_trades if total_trades else 0
    avg_win = np.mean([t["pnl"] for t in winning]) if winning else 0
    avg_loss = abs(np.mean([t["pnl"] for t in losing])) if losing else 0
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")
    if not winning:
        pl_ratio = float("nan")

    avg_days = np.mean([t.get("holding_days", 0) for t in sell_trades]) if sell_trades else 0

    max_cw = max_cl = cw = cl = 0
    for t in sell_trades:
        if t.get("pnl", 0) > 0:
            cw += 1
            cl = 0
        else:
            cl += 1
            cw = 0
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    return {
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe_ratio": round(sharpe, 2),
        "win_rate": round(win_rate, 4),
        "profit_loss_ratio": round(pl_ratio, 2),
        "total_trades": total_trades,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "avg_holding_days": round(avg_days, 1),
        "max_consecutive_wins": max_cw,
        "max_consecutive_losses": max_cl,
    }


def diagnose(metrics: dict) -> dict:
    strengths, weaknesses, suggestions = [], [], []
    wr = metrics["win_rate"]
    if wr >= 0.6:
        strengths.append(f"胜率较高 ({round(wr * 100, 1)}%)")
    elif wr < 0.4:
        weaknesses.append(f"胜率偏低 ({round(wr * 100, 1)}%)")

    plr = metrics["profit_loss_ratio"]
    if plr is not None and not math.isnan(plr):
        if plr >= 2:
            strengths.append(f"盈亏比优秀 ({plr}:1)")
        elif plr < 1:
            weaknesses.append(f"盈亏比不足 ({plr}:1)")
            suggestions.append("考虑提高止盈目标或收紧止损")

    mdd = abs(metrics["max_drawdown"])
    if mdd > 0.2:
        weaknesses.append(f"最大回撤较大 ({round(mdd * 100, 1)}%)")
        suggestions.append("建议增加止损保护或减小仓位比例")
    elif mdd < 0.1:
        strengths.append(f"回撤控制良好 ({round(mdd * 100, 1)}%)")

    sr = metrics["sharpe_ratio"]
    if sr > 1.5:
        strengths.append(f"夏普比率优秀 ({sr})")
    elif sr < 0.5:
        weaknesses.append(f"风险调整收益较低（夏普={sr}）")

    if metrics["total_trades"] < 5:
        weaknesses.append("交易次数过少，统计意义有限")
        suggestions.append("考虑放宽入场条件或延长回测周期")

    return {"strengths": strengths, "weaknesses": weaknesses, "suggestions": suggestions}


def diagnose_advanced(metrics: dict, trades: list, equity_curve: list, strategy: dict) -> dict:
    """Extra agent-friendly diagnostics: failure reason, regime fit, parameter hints."""
    exit_cfg = strategy.get("exit") or {}
    sl_val = exit_cfg.get("stop_loss")
    stop_loss = float(sl_val) if sl_val is not None else -0.10
    tp_val = exit_cfg.get("take_profit")
    take_profit = float(tp_val) if tp_val is not None else 0.50

    total = metrics.get("total_trades", 0)
    win_rate = metrics.get("win_rate", 0)
    plr = metrics.get("profit_loss_ratio", 0)
    mdd = abs(metrics.get("max_drawdown", 0))
    total_return = metrics.get("total_return", 0)

    # trade_count_quality
    if total < 5:
        trade_count_quality = "too_low"
    elif total < 15:
        trade_count_quality = "marginal"
    else:
        trade_count_quality = "sufficient"

    # main_failure_reason — pick the most impactful weakness
    sell_trades = [t for t in trades if t.get("type") == "sell"]
    stop_hits = sum(
        1
        for t in sell_trades
        if (t.get("reason") or "").startswith(("stop_loss", "止损触发", "移动止损触发"))
        or (t.get("pnl_pct", 0) is not None and t.get("pnl_pct", 0) <= stop_loss + 0.005)
    )
    stop_ratio = stop_hits / max(1, len(sell_trades))

    main_failure_reason = None
    if total_return < 0:
        if stop_ratio > 0.5:
            main_failure_reason = "止损频繁触发，可能过紧或市场震荡不匹配"
        elif win_rate < 0.35:
            main_failure_reason = "胜率过低，入场信号质量差"
        elif plr < 1:
            main_failure_reason = "盈亏比不足，盈利未能覆盖亏损"
        else:
            main_failure_reason = "整体信号频次或时机不佳"
    elif mdd > 0.25:
        main_failure_reason = "收益为正但回撤过大，风险控制不足"

    # regime_fit — infer from equity curve shape
    regime_fit = "unknown"
    if len(equity_curve) >= 3:
        # split into halves, compare
        mid = len(equity_curve) // 2
        first = equity_curve[mid]["equity"]
        last = equity_curve[-1]["equity"]
        start_v = equity_curve[0]["equity"]
        first_half = (first - start_v) / start_v if start_v else 0
        second_half = (last - first) / first if first else 0
        if first_half > 0.05 and second_half > 0.05:
            regime_fit = "consistent_across_periods"
        elif first_half > 0.05 and second_half < -0.02:
            regime_fit = "better_in_early_period"
        elif first_half < -0.02 and second_half > 0.05:
            regime_fit = "better_in_late_period"
        elif win_rate > 0.5 and total_return > 0:
            regime_fit = "better_in_trend_market"
        else:
            regime_fit = "regime_dependent"

    # suggested_parameter_changes
    suggestions = []
    if stop_ratio > 0.5 and total_return < 0:
        suggestions.append(
            {
                "parameter": "exit.stop_loss",
                "from": stop_loss,
                "to": round(stop_loss * 1.5, 3),
                "reason": "止损过紧，被震荡打出",
            }
        )
    if plr < 1 and win_rate > 0.4:
        suggestions.append(
            {
                "parameter": "exit.take_profit",
                "from": take_profit,
                "to": round(take_profit * 1.3, 3),
                "reason": "止盈过早，盈亏比偏低",
            }
        )
    if trade_count_quality == "too_low":
        suggestions.append(
            {
                "parameter": "entry.*",
                "from": None,
                "to": None,
                "reason": "入场条件过严，交易次数不足",
            }
        )
    if mdd > 0.25:
        suggestions.append(
            {
                "parameter": "position.size",
                "from": None,
                "to": None,
                "reason": "回撤大，考虑降低仓位比例",
            }
        )

    return {
        "trade_count_quality": trade_count_quality,
        "main_failure_reason": main_failure_reason,
        "regime_fit": regime_fit,
        "stop_loss_hit_ratio": round(stop_ratio, 2),
        "suggested_parameter_changes": suggestions,
    }


def sample_curve(curve: list, max_points: int = 200) -> list:
    if len(curve) <= max_points:
        return curve
    step = len(curve) / max_points
    indices = [int(i * step) for i in range(max_points)]
    if indices[-1] != len(curve) - 1:
        indices.append(len(curve) - 1)
    return [curve[i] for i in indices]


# --------------- Entry Points ---------------


def _benchmark_comparison(
    symbol: str, start: str, end: str, strategy_return: float, execution: str = "next_open"
) -> dict:
    """Buy-and-hold benchmark over the same window; degrades to an error note on fetch failure."""
    try:
        df = fetch_kline(symbol, start, end)
        _validate_adjustment_basis(df)
    except Exception as e:
        return {"symbol": symbol, "error": error_message(e)}
    if len(df) < 2:
        return {"symbol": symbol, "error": f"Insufficient benchmark data: {len(df)} rows"}
    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().any():
        return {"symbol": symbol, "error": "Benchmark dates must be valid"}
    actual_start, actual_end = str(dates.iloc[0].date()), str(dates.iloc[-1].date())
    if (actual_start, actual_end) != (start, end):
        return {
            "symbol": symbol,
            "error": f"Benchmark data covers {actual_start} to {actual_end}; requested {start} to {end}",
        }
    entry_column = "open" if execution == "next_open" else "close"
    first, last = float(df[entry_column].iloc[0]), float(df["close"].iloc[-1])
    if not all(math.isfinite(price) and price > 0 for price in (first, last)):
        return {"symbol": symbol, "error": "Benchmark first and last prices must be finite and positive"}
    total_return = (last - first) / first
    return {
        "symbol": symbol,
        "total_return": round(total_return, 4),
        "excess_return": round(strategy_return - total_return, 4),
    }


def run_backtest(strategy_path: str, symbol: str, start: str | None, end: str | None, capital: float) -> dict:
    strategy = load_strategy(strategy_path)

    if not end:
        end = datetime.now().strftime("%Y-%m-%d")
    if not start:
        start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    symbols = [s.strip() for s in str(symbol).split(",") if s.strip()]
    if not symbols:
        return {"error": "At least one stock symbol is required"}
    use_portfolio = (
        len(symbols) > 1
        or bool(strategy.get("portfolio"))
        or bool(strategy.get("dca"))
        or (strategy.get("position") or {}).get("max_positions", 1) != 1
    )
    if use_portfolio and len(symbols) == 1 and not strategy.get("portfolio") and not strategy.get("dca"):
        # Keep single-symbol max_positions != 1 validation error in single-position simulate()
        use_portfolio = False

    if use_portfolio:
        from concurrent.futures import ThreadPoolExecutor

        # Validate canonical symbol uniqueness before issuing any fetches
        seen_canon: dict[str, str] = {}
        for sym in symbols:
            canon = _canonical_symbol(sym)
            if canon in seen_canon:
                return {"error": f"Duplicate instrument in portfolio: {seen_canon[canon]!r} and {sym!r}"}
            seen_canon[canon] = sym

        warmup = _strategy_warmup_bars(strategy)
        datasets: dict[str, pd.DataFrame] = {}
        actual_starts, actual_ends = [], []
        with ThreadPoolExecutor(max_workers=min(len(symbols), 4)) as pool:
            fetched = {sym: pool.submit(fetch_kline, sym, start, end, True, warmup) for sym in symbols}
            for sym in symbols:
                df = fetched[sym].result()
                ev = df[(df["date"] >= start) & (df["date"] <= end)]
                if len(ev) < 30:
                    return {"error": f"Insufficient data for {sym}: {len(ev)} rows (need at least 30)"}
                datasets[sym] = df
                actual_starts.append(ev["date"].iloc[0].strftime("%Y-%m-%d"))
                actual_ends.append(ev["date"].iloc[-1].strftime("%Y-%m-%d"))
        requested_period = f"{start} to {end}"
        actual_start = min(actual_starts)
        actual_end = max(actual_ends)
        warnings = []
        if actual_start != start or actual_end != end:
            warnings.append(f"Actual data covers {actual_start} to {actual_end}; requested {requested_period}")
        execution = (strategy.get("simulation") or {}).get("execution", "next_open")
        sim = simulate_portfolio(datasets, strategy, capital, start=actual_start)
        final = sim["final_equity"]
        total_in = sim["total_contributed"]
        has_dca = bool(sim.get("contributions"))
        risk_curve = sim["twr_curve"] if has_dca else sim["equity_curve"]
        metrics = compute_metrics(
            sim["trades"],
            sim["equity_curve"],
            total_in,
            final,
            actual_start,
            actual_end,
            risk_curve=risk_curve,
            risk_initial=capital,
            annual_return_basis=sim["time_weighted_return"] if has_dca else None,
        )
        if has_dca:
            metrics["time_weighted_return"] = sim["time_weighted_return"]
        result = {
            "strategy": strategy.get("name", "unnamed"),
            "symbol": symbol,
            "symbols": symbols,
            "mode": sim["mode"],
            "currency": sim["currency"],
            "period": f"{actual_start} to {actual_end}",
            "requested_period": requested_period,
            "warnings": warnings,
            "initial_capital": capital,
            "total_contributed": total_in,
            "final_capital": final,
            "final_cash": sim["final_cash"],
            "final_holdings": sim["final_holdings"],
            "portfolio": sim["portfolio"],
            "costs": sim["costs"],
            "execution": sim["execution"],
            "metrics": metrics,
            "trades": sim["trades"],
            "equity_curve": sample_curve(sim["equity_curve"]),
            "diagnosis": diagnose(metrics),
            "diagnostics": diagnose_advanced(metrics, sim["trades"], risk_curve, strategy),
        }
        if sim.get("contributions"):
            result["contributions"] = sim["contributions"]
        benchmark_symbol = strategy.get("benchmark")
        if benchmark_symbol:
            benchmark_start = sim["equity_curve"][0]["date"] if sim["equity_curve"] else actual_start
            comp_ret = sim["time_weighted_return"] if sim.get("contributions") else metrics["total_return"]
            result["benchmark"] = _benchmark_comparison(
                str(benchmark_symbol), benchmark_start, actual_end, comp_ret, execution
            )
        return result

    df = fetch_kline(symbol, start, end, True, _strategy_warmup_bars(strategy))
    evaluation = df[(df["date"] >= start) & (df["date"] <= end)]
    if len(evaluation) < 30:
        return {"error": f"Insufficient data: {len(evaluation)} rows (need at least 30)"}

    requested_period = f"{start} to {end}"
    actual_start = evaluation["date"].iloc[0].strftime("%Y-%m-%d")
    actual_end = evaluation["date"].iloc[-1].strftime("%Y-%m-%d")
    warnings = []
    if actual_start != start or actual_end != end:
        warnings.append(f"Actual data covers {actual_start} to {actual_end}; requested {requested_period}")
    start, end = actual_start, actual_end
    execution = strategy.get("simulation", {}).get("execution", "next_open")
    warnings.extend(_entry_warmup_warnings(df, strategy, start, execution))
    sim = simulate(df, strategy, capital, symbol, start=start)
    final = sim["final_equity"]
    metrics = compute_metrics(sim["trades"], sim["equity_curve"], capital, final, start, end)

    result = {
        "strategy": strategy.get("name", "unnamed"),
        "symbol": symbol,
        "period": f"{start} to {end}",
        "requested_period": requested_period,
        "warnings": warnings,
        "initial_capital": capital,
        "final_capital": final,
        "costs": sim["costs"],
        "execution": sim["execution"],
        "metrics": metrics,
        "trades": sim["trades"],
        "equity_curve": sample_curve(sim["equity_curve"]),
        "diagnosis": diagnose(metrics),
        "diagnostics": diagnose_advanced(metrics, sim["trades"], sim["equity_curve"], strategy),
    }
    benchmark_symbol = strategy.get("benchmark")
    if benchmark_symbol:
        benchmark_start = sim["equity_curve"][0]["date"] if sim["equity_curve"] else start
        result["benchmark"] = _benchmark_comparison(
            str(benchmark_symbol), benchmark_start, end, metrics["total_return"], execution
        )
    return result


def evaluate_result(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    trades = data.get("trades", [])
    curve = data.get("equity_curve", [])
    initial = data.get("initial_capital", 1000000)
    final = data.get("final_capital", initial)
    period = data.get("period", "unknown")
    parts = period.split(" to ") if " to " in period else [period, period]
    metrics = data.get("metrics")
    if metrics is None:
        metrics = compute_metrics(trades, curve, initial, final, parts[0], parts[1])
    data["metrics"] = metrics
    data["diagnosis"] = diagnose(metrics)
    return data


# --------------- Signal Evaluation ---------------


def _signal_context(df: pd.DataFrame) -> dict:
    """Precompute every indicator series the signal checkers need, once per
    evaluate_signal call (the old lambdas recomputed them per bar — O(n²))."""
    close = df["close"]
    return {
        "dif": compute_macd_dif(close),
        "dea": compute_macd_dea(close),
        "rsi14": compute_rsi(close, 14),
        "ma5": compute_ma(close, 5),
        "ma20": compute_ma(close, 20),
        "close": close,
        "high": df["high"],
        "low": df["low"],
        "volume": df["volume"],
    }


SIGNAL_DEFINITIONS = {
    "macd_golden_cross": lambda c, i: (
        i >= 1
        and float(c["dif"].iloc[i]) > float(c["dea"].iloc[i])
        and float(c["dif"].iloc[i - 1]) <= float(c["dea"].iloc[i - 1])
    ),
    "macd_death_cross": lambda c, i: (
        i >= 1
        and float(c["dif"].iloc[i]) < float(c["dea"].iloc[i])
        and float(c["dif"].iloc[i - 1]) >= float(c["dea"].iloc[i - 1])
    ),
    "rsi_oversold": lambda c, i: float(c["rsi14"].iloc[i]) < 30,
    "rsi_overbought": lambda c, i: float(c["rsi14"].iloc[i]) > 70,
    "breakout_20d": lambda c, i: (
        i >= 20
        and float(c["close"].iloc[i]) > float(c["high"].iloc[i - 20 : i].max())
        and float(c["close"].iloc[i - 1]) <= float(c["high"].iloc[i - 20 : i].max())
    ),
    "breakdown_20d": lambda c, i: (
        i >= 20
        and float(c["close"].iloc[i]) < float(c["low"].iloc[i - 20 : i].min())
        and float(c["close"].iloc[i - 1]) >= float(c["low"].iloc[i - 20 : i].min())
    ),
    "volume_surge": lambda c, i: (
        i >= 20 and float(c["volume"].iloc[i]) > 2.0 * float(c["volume"].iloc[i - 20 : i].mean())
    ),
    "ma_golden_cross": lambda c, i: (
        i >= 20
        and float(c["ma5"].iloc[i]) > float(c["ma20"].iloc[i])
        and float(c["ma5"].iloc[i - 1]) <= float(c["ma20"].iloc[i - 1])
    ),
    "ma_death_cross": lambda c, i: (
        i >= 20
        and float(c["ma5"].iloc[i]) < float(c["ma20"].iloc[i])
        and float(c["ma5"].iloc[i - 1]) >= float(c["ma20"].iloc[i - 1])
    ),
}


def evaluate_signal(symbol: str, signal_name: str, forward_days: list[int] = None, lookback: int = 250) -> dict:
    if forward_days is None:
        forward_days = [3, 5, 10]
    if signal_name not in SIGNAL_DEFINITIONS:
        return {"error": f"Unknown signal: {signal_name}. Available: {list(SIGNAL_DEFINITIONS.keys())}"}

    checker = SIGNAL_DEFINITIONS[signal_name]
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=int(lookback * 1.5))).strftime("%Y-%m-%d")
    df = fetch_kline(symbol, start, end)
    if len(df) < 30:
        return {"error": f"Insufficient data: {len(df)} rows"}

    max_fwd = max(forward_days)
    ctx = _signal_context(df)
    occurrences = []
    for i in range(20, len(df) - max_fwd):
        try:
            if checker(ctx, i):
                entry_price = float(df["close"].iloc[i])
                date_str = (
                    str(df["date"].iloc[i].date()) if hasattr(df["date"].iloc[i], "date") else str(df["date"].iloc[i])
                )
                fwd_returns = {}
                for fd in forward_days:
                    if i + fd < len(df):
                        fwd_price = float(df["close"].iloc[i + fd])
                        fwd_returns[f"return_{fd}d"] = round((fwd_price - entry_price) / entry_price * 100, 2)
                occurrences.append({"date": date_str, "price": round(entry_price, 2), **fwd_returns})
        except Exception:
            continue

    if not occurrences:
        return {"signal": signal_name, "symbol": symbol, "occurrences": 0, "note": "No signal found in period"}

    result = {"signal": signal_name, "symbol": symbol, "occurrences": len(occurrences)}
    for fd in forward_days:
        key = f"return_{fd}d"
        vals = [o[key] for o in occurrences if key in o]
        if vals:
            result[f"avg_return_{fd}d"] = round(np.mean(vals), 2)
            result[f"win_rate_{fd}d"] = round(sum(1 for v in vals if v > 0) / len(vals), 4)
    result["samples"] = occurrences[-10:]
    return result


def main():
    parser = argparse.ArgumentParser(description="AlphaEvo strategy backtester")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run")
    p_run.add_argument("strategy", help="Path to strategy YAML file")
    p_run.add_argument("symbol", help="Stock symbol")
    p_run.add_argument("--start", help="Start date (YYYY-MM-DD)")
    p_run.add_argument("--end", help="End date (YYYY-MM-DD)")
    p_run.add_argument("--capital", type=float, default=1000000)

    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("result", help="Path to result JSON file")

    p_sig = sub.add_parser("evaluate_signal")
    p_sig.add_argument("symbol", help="Stock symbol")
    p_sig.add_argument("signal", help="Signal name (e.g. macd_golden_cross)")
    p_sig.add_argument("--forward", default="3,5,10", help="Comma-separated forward days")
    p_sig.add_argument("--lookback", type=int, default=250)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "run":
            result = run_backtest(args.strategy, args.symbol, args.start, args.end, args.capital)
        elif args.command == "evaluate":
            result = evaluate_result(args.result)
        elif args.command == "evaluate_signal":
            fwd = [int(x) for x in args.forward.split(",")]
            result = evaluate_signal(args.symbol, args.signal, fwd, args.lookback)
        else:
            result = {"error": f"Unknown command: {args.command}"}
    except Exception as e:
        # A traceback would leave stdout empty; hosts need valid JSON even on failure.
        print(json.dumps({"error": error_message(e)}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(json_safe(result), ensure_ascii=False, default=str))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    utf8_stdio()
    main()
