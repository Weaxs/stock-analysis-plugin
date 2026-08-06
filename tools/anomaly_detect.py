#!/usr/bin/env python3
"""Anomaly/event detection — detect edge-crossing transitions and unusual conditions."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stock_data import detect_market, normalize_stock_code
from technical import fetch_kline, to_dataframe

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _run_tool(script: str, args: str):
    cmd = f"{sys.executable} {TOOLS_DIR}/{script} {args}"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def _anomaly(type_: str, severity: str, direction: str, description: str, **extra) -> dict:
    result = {
        "type": type_,
        "severity": severity,
        "direction": direction,
        "description": description,
    }
    result.update(extra)
    return result


# --------------- Detectors ---------------


def detect_macd_cross(close: pd.Series) -> list:
    if len(close) < 30:
        return []
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()

    prev_diff = float(dif.iloc[-2] - dea.iloc[-2])
    curr_diff = float(dif.iloc[-1] - dea.iloc[-1])

    anomalies = []
    if prev_diff <= 0 and curr_diff > 0:
        above_zero = float(dif.iloc[-1]) > 0
        sev = "high" if above_zero else "medium"
        desc = "MACD金叉" + ("（零轴上方，强势信号）" if above_zero else "（零轴下方）")
        anomalies.append(_anomaly("macd_golden_cross", sev, "bullish", desc))
    elif prev_diff >= 0 and curr_diff < 0:
        below_zero = float(dif.iloc[-1]) < 0
        sev = "high" if below_zero else "medium"
        desc = "MACD死叉" + ("（零轴下方，弱势信号）" if below_zero else "（零轴上方）")
        anomalies.append(_anomaly("macd_death_cross", sev, "bearish", desc))
    return anomalies


def detect_rsi_extreme(close: pd.Series) -> list:
    if len(close) < 10:
        return []
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(6).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(6).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    if len(rsi.dropna()) < 2:
        return []

    curr = float(rsi.iloc[-1])
    prev = float(rsi.iloc[-2])
    anomalies = []

    if curr > 80 and prev <= 80:
        anomalies.append(
            _anomaly(
                "rsi_overbought_entry",
                "medium",
                "bearish",
                f"RSI6进入超买区（{curr:.1f}），短期回调风险增大",
                rsi6=round(curr, 2),
            )
        )
    elif curr < 20 and prev >= 20:
        anomalies.append(
            _anomaly(
                "rsi_oversold_entry",
                "medium",
                "bullish",
                f"RSI6进入超卖区（{curr:.1f}），关注反弹机会",
                rsi6=round(curr, 2),
            )
        )
    return anomalies


def detect_price_breakout(df: pd.DataFrame) -> list:
    if len(df) < 21:
        return []
    close = df["close"]
    high = df["high"]
    low = df["low"]

    prev_20_high = float(high.iloc[-21:-1].max())
    prev_20_low = float(low.iloc[-21:-1].min())
    curr_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])

    anomalies = []
    if curr_close > prev_20_high and prev_close <= prev_20_high:
        anomalies.append(
            _anomaly(
                "breakout_20d_high",
                "high",
                "bullish",
                f"突破20日高点{prev_20_high:.2f}，创阶段新高",
                level=round(prev_20_high, 2),
            )
        )
    if curr_close < prev_20_low and prev_close >= prev_20_low:
        anomalies.append(
            _anomaly(
                "breakdown_20d_low",
                "high",
                "bearish",
                f"跌破20日低点{prev_20_low:.2f}，创阶段新低",
                level=round(prev_20_low, 2),
            )
        )
    return anomalies


def detect_volume_spike(volume: pd.Series) -> list:
    if len(volume) < 6:
        return []
    vol_ma5 = float(volume.shift(1).rolling(5).mean().iloc[-1])
    cur_vol = float(volume.iloc[-1])
    if vol_ma5 <= 0 or np.isnan(vol_ma5):
        return []

    ratio = cur_vol / vol_ma5
    anomalies = []
    if ratio > 3:
        anomalies.append(
            _anomaly(
                "volume_spike_extreme",
                "high",
                "neutral",
                f"成交量异常放大（量比{ratio:.1f}x），远超5日均量",
                volume_ratio=round(ratio, 2),
            )
        )
    elif ratio > 2:
        anomalies.append(
            _anomaly(
                "volume_spike",
                "medium",
                "neutral",
                f"成交量明显放大（量比{ratio:.1f}x），市场关注度骤升",
                volume_ratio=round(ratio, 2),
            )
        )
    return anomalies


def detect_bollinger_breakout(close: pd.Series) -> list:
    if len(close) < 21:
        return []
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = mid + 2 * std
    lower = mid - 2 * std

    curr_price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    curr_upper = float(upper.iloc[-1])
    curr_lower = float(lower.iloc[-1])
    prev_upper = float(upper.iloc[-2])
    prev_lower = float(lower.iloc[-2])

    anomalies = []
    if curr_price > curr_upper and prev_price <= prev_upper:
        anomalies.append(
            _anomaly(
                "bollinger_upper_break",
                "medium",
                "bearish",
                f"突破布林带上轨（{curr_upper:.2f}），短期超买或加速上涨",
                upper=round(curr_upper, 2),
            )
        )
    if curr_price < curr_lower and prev_price >= prev_lower:
        anomalies.append(
            _anomaly(
                "bollinger_lower_break",
                "medium",
                "bullish",
                f"跌破布林带下轨（{curr_lower:.2f}），短期超卖或加速下跌",
                lower=round(curr_lower, 2),
            )
        )
    return anomalies


def detect_kdj_extreme(high: pd.Series, low: pd.Series, close: pd.Series) -> list:
    if len(close) < 10:
        return []
    low_n = low.rolling(9).min()
    high_n = high.rolling(9).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d

    if len(j.dropna()) < 2:
        return []

    curr_j = float(j.iloc[-1])
    prev_j = float(j.iloc[-2])
    anomalies = []

    if curr_j > 80 and prev_j <= 80:
        anomalies.append(
            _anomaly(
                "kdj_overbought_entry",
                "low",
                "bearish",
                f"KDJ-J值进入超买区（{curr_j:.1f}），短期高位风险",
                j_value=round(curr_j, 2),
            )
        )
    elif curr_j < 20 and prev_j >= 20:
        anomalies.append(
            _anomaly(
                "kdj_oversold_entry",
                "low",
                "bullish",
                f"KDJ-J值进入超卖区（{curr_j:.1f}），关注低吸机会",
                j_value=round(curr_j, 2),
            )
        )
    return anomalies


def detect_large_move(quote: dict) -> list:
    change_pct = quote.get("change_pct")
    if change_pct is None:
        return []
    change_pct = float(change_pct)
    anomalies = []

    if change_pct > 7:
        anomalies.append(
            _anomaly(
                "large_move_up",
                "high",
                "bullish",
                f"单日大涨{change_pct:.2f}%，可能有重大利好或资金抢筹",
                change_pct=round(change_pct, 2),
            )
        )
    elif change_pct > 5:
        anomalies.append(
            _anomaly(
                "large_move_up",
                "medium",
                "bullish",
                f"单日涨幅{change_pct:.2f}%，显著上涨",
                change_pct=round(change_pct, 2),
            )
        )
    elif change_pct < -7:
        anomalies.append(
            _anomaly(
                "large_move_down",
                "high",
                "bearish",
                f"单日大跌{change_pct:.2f}%，可能有重大利空或恐慌抛售",
                change_pct=round(change_pct, 2),
            )
        )
    elif change_pct < -5:
        anomalies.append(
            _anomaly(
                "large_move_down",
                "medium",
                "bearish",
                f"单日跌幅{change_pct:.2f}%，显著下跌",
                change_pct=round(change_pct, 2),
            )
        )
    return anomalies


def detect_limit_hit(symbol: str, quote: dict) -> list:
    market = detect_market(symbol)
    if market != "A":
        return []

    price = quote.get("price")
    prev_close = quote.get("prev_close")
    change_pct = quote.get("change_pct")
    if price is None or prev_close is None:
        return []

    code_info = normalize_stock_code(symbol)
    limit_pct = code_info.get("limit_pct")
    if limit_pct is None:
        return []

    name = quote.get("name", "")
    if name and "ST" in name.upper() and code_info["board"] == "main":
        limit_pct = 0.05

    prev_close = float(prev_close)
    price = float(price)
    limit_up_price = round(prev_close * (1 + limit_pct), 2)
    limit_down_price = round(prev_close * (1 - limit_pct), 2)

    anomalies = []
    if abs(price - limit_up_price) < 0.02:
        pct_str = f"{change_pct:.2f}" if change_pct is not None else f"{limit_pct * 100:.0f}"
        anomalies.append(_anomaly("limit_up", "high", "bullish", f"涨停（涨幅{pct_str}%），封板中"))
    elif abs(price - limit_down_price) < 0.02:
        pct_str = f"{change_pct:.2f}" if change_pct is not None else f"-{limit_pct * 100:.0f}"
        anomalies.append(_anomaly("limit_down", "high", "bearish", f"跌停（跌幅{pct_str}%），恐慌情绪"))
    return anomalies


def detect_capital_flow_anomaly(symbol: str) -> list:
    market = detect_market(symbol)
    if market != "A":
        return []

    flow_data = _run_tool("stock_data.py", f"capital_flow {symbol} --mode detail")
    if not flow_data or not isinstance(flow_data, list) or len(flow_data) < 3:
        return []

    try:
        inflows = [float(d.get("main_net_inflow", 0)) for d in flow_data if d.get("main_net_inflow") is not None]
    except (ValueError, TypeError):
        return []

    if len(inflows) < 3:
        return []

    latest = inflows[-1]
    history = inflows[:-1]
    avg = sum(history) / len(history)
    std_val = float(np.std(history)) if len(history) > 2 else abs(avg) * 0.5

    if std_val == 0:
        return []

    z_score = (latest - avg) / std_val
    anomalies = []

    if z_score > 2:
        anomalies.append(
            _anomaly(
                "capital_inflow_surge",
                "medium",
                "bullish",
                f"主力资金大幅净流入（z={z_score:.1f}），显著高于近期均值",
                net_inflow=latest,
            )
        )
    elif z_score < -2:
        anomalies.append(
            _anomaly(
                "capital_outflow_surge",
                "medium",
                "bearish",
                f"主力资金大幅净流出（z={z_score:.1f}），显著高于近期均值",
                net_inflow=latest,
            )
        )
    return anomalies


def detect_divergence(df: pd.DataFrame) -> list:
    if len(df) < 30:
        return []
    close = df["close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26

    window = min(20, len(df) - 10)
    recent_close = close.iloc[-window:]
    recent_dif = dif.iloc[-window:]
    prior_close = close.iloc[-window * 2 : -window]
    prior_dif = dif.iloc[-window * 2 : -window]

    if len(prior_close) < 5:
        return []

    anomalies = []
    if float(recent_close.max()) > float(prior_close.max()) and float(recent_dif.max()) < float(prior_dif.max()):
        anomalies.append(
            _anomaly(
                "top_divergence",
                "high",
                "bearish",
                "顶背离 — 价格创新高但MACD动能减弱，警惕回调",
            )
        )
    if float(recent_close.min()) < float(prior_close.min()) and float(recent_dif.min()) > float(prior_dif.min()):
        anomalies.append(
            _anomaly(
                "bottom_divergence",
                "high",
                "bullish",
                "底背离 — 价格创新低但MACD动能增强，关注反转机会",
            )
        )
    return anomalies


def detect_ma_cross(close: pd.Series) -> list:
    if len(close) < 21:
        return []
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()

    anomalies = []

    if float(ma5.iloc[-2]) <= float(ma10.iloc[-2]) and float(ma5.iloc[-1]) > float(ma10.iloc[-1]):
        anomalies.append(_anomaly("ma5_cross_ma10_golden", "medium", "bullish", "MA5上穿MA10，短线金叉"))
    elif float(ma5.iloc[-2]) >= float(ma10.iloc[-2]) and float(ma5.iloc[-1]) < float(ma10.iloc[-1]):
        anomalies.append(_anomaly("ma5_cross_ma10_death", "medium", "bearish", "MA5下穿MA10，短线死叉"))

    if float(ma10.iloc[-2]) <= float(ma20.iloc[-2]) and float(ma10.iloc[-1]) > float(ma20.iloc[-1]):
        anomalies.append(_anomaly("ma10_cross_ma20_golden", "medium", "bullish", "MA10上穿MA20，中线金叉"))
    elif float(ma10.iloc[-2]) >= float(ma20.iloc[-2]) and float(ma10.iloc[-1]) < float(ma20.iloc[-1]):
        anomalies.append(_anomaly("ma10_cross_ma20_death", "medium", "bearish", "MA10下穿MA20，中线死叉"))

    return anomalies


def detect_gap(df: pd.DataFrame) -> list:
    if len(df) < 2:
        return []
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    anomalies = []
    if float(curr["low"]) > float(prev["high"]):
        gap_pct = (float(curr["low"]) - float(prev["high"])) / float(prev["close"]) * 100
        anomalies.append(
            _anomaly(
                "gap_up",
                "high" if gap_pct > 3 else "medium",
                "bullish",
                f"向上跳空缺口（{gap_pct:.2f}%），强势突破信号",
                gap_pct=round(gap_pct, 2),
            )
        )
    elif float(curr["high"]) < float(prev["low"]):
        gap_pct = (float(prev["low"]) - float(curr["high"])) / float(prev["close"]) * 100
        anomalies.append(
            _anomaly(
                "gap_down",
                "high" if gap_pct > 3 else "medium",
                "bearish",
                f"向下跳空缺口（{gap_pct:.2f}%），弱势破位信号",
                gap_pct=round(gap_pct, 2),
            )
        )
    return anomalies


# --------------- Orchestration ---------------


def detect_anomalies(symbol: str) -> dict:
    records = fetch_kline(symbol, "daily", 60)
    if isinstance(records, dict) and "error" in records:
        return {"symbol": symbol, "error": records.get("error", "Failed to fetch kline")}
    if not records:
        return {"symbol": symbol, "error": "No kline data returned"}

    df = to_dataframe(records)
    if len(df) < 10:
        return {"symbol": symbol, "error": f"Insufficient data: {len(df)} rows"}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    quote = _run_tool("stock_data.py", f"quote {symbol}") or {}

    anomalies = []
    anomalies.extend(detect_macd_cross(close))
    anomalies.extend(detect_rsi_extreme(close))
    anomalies.extend(detect_price_breakout(df))
    anomalies.extend(detect_volume_spike(volume))
    anomalies.extend(detect_bollinger_breakout(close))
    anomalies.extend(detect_kdj_extreme(high, low, close))
    anomalies.extend(detect_divergence(df))
    anomalies.extend(detect_ma_cross(close))
    anomalies.extend(detect_gap(df))

    if quote and "error" not in quote:
        anomalies.extend(detect_limit_hit(symbol, quote))
        anomalies.extend(detect_large_move(quote))

    anomalies.extend(detect_capital_flow_anomaly(symbol))

    severity_order = {"high": 0, "medium": 1, "low": 2}
    anomalies.sort(key=lambda a: severity_order.get(a["severity"], 3))

    high_count = sum(1 for a in anomalies if a["severity"] == "high")
    bullish_count = sum(1 for a in anomalies if a["direction"] == "bullish")
    bearish_count = sum(1 for a in anomalies if a["direction"] == "bearish")

    return {
        "symbol": symbol,
        "anomaly_count": len(anomalies),
        "summary": {
            "high_severity": high_count,
            "bullish_signals": bullish_count,
            "bearish_signals": bearish_count,
            "overall_bias": (
                "bullish"
                if bullish_count > bearish_count
                else "bearish"
                if bearish_count > bullish_count
                else "neutral"
            ),
        },
        "anomalies": anomalies,
    }


def main():
    parser = argparse.ArgumentParser(description="Anomaly/event detection")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("detect")
    p.add_argument("symbol", help="Stock symbol (e.g. 600519, AAPL, 00700.HK)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = detect_anomalies(args.symbol)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
