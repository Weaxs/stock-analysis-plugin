#!/usr/bin/env python3
"""AlphaEvo — strategy backtesting engine with YAML DSL."""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yaml


# --------------- Strategy Loading ---------------

def load_strategy(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        strat = yaml.safe_load(f)
    params = strat.get("parameters", {})
    _resolve_refs(strat, params)
    return strat


def _resolve_refs(obj, params: dict):
    if isinstance(obj, dict):
        for k, v in obj.items():
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

def fetch_kline(symbol: str, start: str | None, end: str | None) -> pd.DataFrame:
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_data.py")
    count = 500
    if start and end:
        d0 = datetime.strptime(start, "%Y-%m-%d")
        d1 = datetime.strptime(end, "%Y-%m-%d")
        count = max((d1 - d0).days + 60, 300)

    r = subprocess.run(
        [sys.executable, script, "kline", symbol, "--period", "daily", "--count", str(count)],
        capture_output=True, text=True,
    )
    data = json.loads(r.stdout)
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(data["error"])

    df = pd.DataFrame(data)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)

    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return df.reset_index(drop=True)


# --------------- Indicators (inline) ---------------

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def compute_ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def compute_macd_dif(close: pd.Series) -> pd.Series:
    return compute_ema(close, 12) - compute_ema(close, 26)


def compute_macd_dea(close: pd.Series) -> pd.Series:
    return compute_macd_dif(close).ewm(span=9, adjust=False).mean()


def compute_macd_hist(close: pd.Series) -> pd.Series:
    return 2 * (compute_macd_dif(close) - compute_macd_dea(close))


def compute_volume_ratio(volume: pd.Series, period: int = 5) -> pd.Series:
    ma = volume.rolling(period).mean()
    return (volume / ma.replace(0, np.nan)).fillna(1)


def compute_price_change(close: pd.Series) -> pd.Series:
    return close.pct_change() * 100


def compute_bollinger_position(close: pd.Series, period: int = 20) -> pd.Series:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    width = (upper - lower).replace(0, np.nan)
    return ((close - lower) / width).fillna(0.5)


def get_indicator(df: pd.DataFrame, name: str, period: int | None = None) -> pd.Series:
    close = df["close"]
    dispatch = {
        "rsi": lambda: compute_rsi(close, period or 14),
        "ma": lambda: compute_ma(close, period or 20),
        "ema": lambda: compute_ema(close, period or 20),
        "macd_dif": lambda: compute_macd_dif(close),
        "macd_dea": lambda: compute_macd_dea(close),
        "macd": lambda: compute_macd_hist(close),
        "volume_ratio": lambda: compute_volume_ratio(df["volume"], period or 5),
        "price_change": lambda: compute_price_change(close),
        "bollinger_position": lambda: compute_bollinger_position(close, period or 20),
        "close": lambda: close,
        "volume": lambda: df["volume"].astype(float),
    }
    fn = dispatch.get(name.lower())
    if fn is None:
        raise ValueError(f"Unknown indicator: {name}")
    return fn()


# --------------- Condition Evaluation ---------------

def check_condition(series: pd.Series, operator: str, value: float, idx: int) -> bool:
    if idx < 1 or idx >= len(series):
        return False
    cur = float(series.iloc[idx])
    ops = {
        ">": cur > value,
        "<": cur < value,
        ">=": cur >= value,
        "<=": cur <= value,
        "==": abs(cur - value) < 1e-6,
    }
    if operator in ops:
        return ops[operator]
    prev = float(series.iloc[idx - 1])
    if operator == "cross_above":
        return prev <= value and cur > value
    if operator == "cross_below":
        return prev >= value and cur < value
    return False


def evaluate_conditions(df: pd.DataFrame, conditions: list, logic: str, idx: int, cache: dict) -> tuple[bool, str]:
    results = []
    reasons = []
    for cond in conditions:
        ind_name = cond["indicator"]
        period = cond.get("period")
        if isinstance(period, str):
            try:
                period = int(float(period))
            except (ValueError, TypeError):
                period = None

        cache_key = f"{ind_name}_{period}"
        if cache_key not in cache:
            cache[cache_key] = get_indicator(df, ind_name, period)
        series = cache[cache_key]

        op = cond["operator"]
        val = float(cond["value"])
        hit = check_condition(series, op, val, idx)
        results.append(hit)
        if hit:
            cur_val = round(float(series.iloc[idx]), 2)
            label = f"{ind_name}({period})" if period else ind_name
            reasons.append(f"{label} = {cur_val} {op} {val}")

    if logic == "all":
        return all(results), "; ".join(reasons)
    return any(results), "; ".join(reasons)


# --------------- Simulation ---------------

def simulate(df: pd.DataFrame, strategy: dict, capital: float, symbol: str) -> dict:
    market = "A" if re.match(r"^\d{6}$", symbol) else "OTHER"
    lot_size = 100 if market == "A" else 1
    slippage = 0.001
    commission = 0.0015

    entry = strategy.get("entry", {})
    exit_ = strategy.get("exit", {})
    position_cfg = strategy.get("position", {})
    size_frac = float(position_cfg.get("size", 1.0))
    stop_loss = float(exit_.get("stop_loss", -0.10))
    take_profit = float(exit_.get("take_profit", 0.50))

    entry_conditions = entry.get("conditions", [])
    entry_logic = entry.get("logic", "all")
    exit_conditions = exit_.get("conditions", [])
    exit_logic = exit_.get("logic", "any")

    cache: dict = {}
    trades = []
    equity_curve = []
    holding = None
    cash = capital
    trade_id = 0

    for i in range(1, len(df)):
        row = df.iloc[i]
        date_str = str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"])
        price = float(row["close"])

        if holding is None:
            hit, reason = evaluate_conditions(df, entry_conditions, entry_logic, i, cache)
            if hit:
                buy_price = price * (1 + slippage)
                buy_amount = cash * size_frac
                shares = int(buy_amount / buy_price / lot_size) * lot_size
                if shares <= 0:
                    continue
                cost = shares * buy_price * (1 + commission)
                cash -= cost
                trade_id += 1
                holding = {"id": trade_id, "buy_date": date_str, "buy_price": round(buy_price, 4),
                           "shares": shares, "cost": cost}
                trades.append({"id": trade_id, "type": "buy", "date": date_str,
                               "price": round(buy_price, 2), "shares": shares,
                               "amount": round(cost, 2), "reason": reason})
        else:
            pnl_pct = (price - holding["buy_price"]) / holding["buy_price"]
            sell = False
            reason = ""

            if pnl_pct <= stop_loss:
                sell, reason = True, f"止损触发 ({round(pnl_pct*100,1)}% <= {round(stop_loss*100,1)}%)"
            elif pnl_pct >= take_profit:
                sell, reason = True, f"止盈触发 ({round(pnl_pct*100,1)}% >= {round(take_profit*100,1)}%)"
            elif exit_conditions:
                sell, reason = evaluate_conditions(df, exit_conditions, exit_logic, i, cache)

            if sell:
                sell_price = price * (1 - slippage)
                proceeds = holding["shares"] * sell_price * (1 - commission)
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
                trades.append({"id": trade_id_sell, "type": "sell", "date": date_str,
                               "price": round(sell_price, 2), "shares": holding["shares"],
                               "amount": round(proceeds, 2), "reason": reason,
                               "pnl": round(pnl, 2),
                               "pnl_pct": round(pnl / holding["cost"], 4),
                               "holding_days": days})
                holding = None

        equity = cash
        if holding:
            equity += holding["shares"] * price
        equity_curve.append({"date": date_str, "equity": round(equity, 2)})

    if holding:
        last_price = float(df["close"].iloc[-1])
        equity = cash + holding["shares"] * last_price
        equity_curve[-1]["equity"] = round(equity, 2)

    return {
        "trades": trades,
        "equity_curve": equity_curve,
        "final_equity": equity_curve[-1]["equity"] if equity_curve else capital,
    }


# --------------- Metrics ---------------

def compute_metrics(trades: list, equity_curve: list, initial: float, final: float, start: str, end: str) -> dict:
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
    annual_return = (1 + total_return) ** (1 / max(years, 0.01)) - 1 if total_return > -1 else total_return

    equities = pd.Series([e["equity"] for e in equity_curve], dtype=float)
    peak = equities.cummax()
    dd = (equities - peak) / peak
    max_drawdown = float(dd.min()) if len(dd) > 0 else 0

    daily_returns = equities.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() - 0.03/252) / daily_returns.std() * math.sqrt(252)
    else:
        sharpe = 0

    win_rate = len(winning) / total_trades if total_trades else 0
    avg_win = np.mean([t["pnl"] for t in winning]) if winning else 0
    avg_loss = abs(np.mean([t["pnl"] for t in losing])) if losing else 1
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

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
        strengths.append(f"胜率较高 ({round(wr*100,1)}%)")
    elif wr < 0.4:
        weaknesses.append(f"胜率偏低 ({round(wr*100,1)}%)")

    plr = metrics["profit_loss_ratio"]
    if plr >= 2:
        strengths.append(f"盈亏比优秀 ({plr}:1)")
    elif plr < 1:
        weaknesses.append(f"盈亏比不足 ({plr}:1)")
        suggestions.append("考虑提高止盈目标或收紧止损")

    mdd = abs(metrics["max_drawdown"])
    if mdd > 0.2:
        weaknesses.append(f"最大回撤较大 ({round(mdd*100,1)}%)")
        suggestions.append("建议增加止损保护或减小仓位比例")
    elif mdd < 0.1:
        strengths.append(f"回撤控制良好 ({round(mdd*100,1)}%)")

    sr = metrics["sharpe_ratio"]
    if sr > 1.5:
        strengths.append(f"夏普比率优秀 ({sr})")
    elif sr < 0.5:
        weaknesses.append(f"风险调整收益较低（夏普={sr}）")

    if metrics["total_trades"] < 5:
        weaknesses.append("交易次数过少，统计意义有限")
        suggestions.append("考虑放宽入场条件或延长回测周期")

    return {"strengths": strengths, "weaknesses": weaknesses, "suggestions": suggestions}


def sample_curve(curve: list, max_points: int = 200) -> list:
    if len(curve) <= max_points:
        return curve
    step = len(curve) / max_points
    indices = [int(i * step) for i in range(max_points)]
    if indices[-1] != len(curve) - 1:
        indices.append(len(curve) - 1)
    return [curve[i] for i in indices]


# --------------- Entry Points ---------------

def run_backtest(strategy_path: str, symbol: str, start: str | None, end: str | None, capital: float) -> dict:
    strategy = load_strategy(strategy_path)

    if not end:
        end = datetime.now().strftime("%Y-%m-%d")
    if not start:
        start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    df = fetch_kline(symbol, start, end)
    if len(df) < 30:
        return {"error": f"Insufficient data: {len(df)} rows (need at least 30)"}

    sim = simulate(df, strategy, capital, symbol)
    final = sim["final_equity"]
    metrics = compute_metrics(sim["trades"], sim["equity_curve"], capital, final, start, end)

    return {
        "strategy": strategy.get("name", "unnamed"),
        "symbol": symbol,
        "period": f"{start} to {end}",
        "initial_capital": capital,
        "final_capital": final,
        "metrics": metrics,
        "trades": sim["trades"],
        "equity_curve": sample_curve(sim["equity_curve"]),
        "diagnosis": diagnose(metrics),
    }


def evaluate_result(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    trades = data.get("trades", [])
    curve = data.get("equity_curve", [])
    initial = data.get("initial_capital", 1000000)
    final = data.get("final_capital", initial)
    period = data.get("period", "unknown")
    parts = period.split(" to ") if " to " in period else [period, period]
    metrics = compute_metrics(trades, curve, initial, final, parts[0], parts[1])
    data["metrics"] = metrics
    data["diagnosis"] = diagnose(metrics)
    return data


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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        result = run_backtest(args.strategy, args.symbol, args.start, args.end, args.capital)
    else:
        result = evaluate_result(args.result)

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
