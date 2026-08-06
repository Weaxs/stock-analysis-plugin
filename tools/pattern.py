#!/usr/bin/env python3
"""K-line pattern recognition — detect 12+ candlestick/chart patterns."""

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd


def fetch_kline(symbol: str, period: str = "daily", count: int = 60) -> list:
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_data.py")
    r = subprocess.run(
        [sys.executable, script, "kline", symbol, "--period", period, "--count", str(count)],
        capture_output=True,
        text=True,
    )
    return json.loads(r.stdout)


def to_dataframe(records: list) -> pd.DataFrame:
    df = pd.DataFrame(records)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
    return df


def _body(row):
    return abs(row["close"] - row["open"])


def _range(row):
    return row["high"] - row["low"]


def _upper_shadow(row):
    return row["high"] - max(row["close"], row["open"])


def _lower_shadow(row):
    return min(row["close"], row["open"]) - row["low"]


def _is_bullish(row):
    return row["close"] > row["open"]


def _is_bearish(row):
    return row["close"] < row["open"]


def _change_pct(row):
    if row["open"] == 0:
        return 0
    return (row["close"] - row["open"]) / row["open"] * 100


def detect_doji(df: pd.DataFrame) -> list:
    """十字星: body < 10% of range, both shadows present."""
    patterns = []
    for i in range(len(df)):
        row = df.iloc[i]
        r = _range(row)
        if r == 0:
            continue
        b = _body(row)
        if b / r < 0.1 and _upper_shadow(row) > r * 0.2 and _lower_shadow(row) > r * 0.2:
            patterns.append(
                {
                    "date": str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"]),
                    "pattern": "doji",
                    "name_cn": "十字星",
                    "direction": "neutral",
                    "description": "多空力量均衡，可能变盘",
                }
            )
    return patterns


def detect_hammer(df: pd.DataFrame) -> list:
    """锤子线: small body at top, long lower shadow >= 2x body, little upper shadow."""
    patterns = []
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        b = _body(row)
        r = _range(row)
        if r == 0 or b == 0:
            continue
        ls = _lower_shadow(row)
        us = _upper_shadow(row)
        if ls >= 2 * b and us < b * 0.5 and _is_bearish(prev):
            patterns.append(
                {
                    "date": str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"]),
                    "pattern": "hammer",
                    "name_cn": "锤子线",
                    "direction": "bullish",
                    "description": "下跌后出现锤子线，看涨反转信号",
                }
            )
    return patterns


def detect_hanging_man(df: pd.DataFrame) -> list:
    """上吊线: same shape as hammer but after uptrend."""
    patterns = []
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        b = _body(row)
        r = _range(row)
        if r == 0 or b == 0:
            continue
        ls = _lower_shadow(row)
        us = _upper_shadow(row)
        if ls >= 2 * b and us < b * 0.5 and _is_bullish(prev):
            patterns.append(
                {
                    "date": str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"]),
                    "pattern": "hanging_man",
                    "name_cn": "上吊线",
                    "direction": "bearish",
                    "description": "上涨后出现上吊线，看跌反转信号",
                }
            )
    return patterns


def detect_shooting_star(df: pd.DataFrame) -> list:
    """流星线: small body at bottom, long upper shadow >= 2x body, little lower shadow, after uptrend."""
    patterns = []
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        b = _body(row)
        if b == 0:
            continue
        us = _upper_shadow(row)
        ls = _lower_shadow(row)
        if us >= 2 * b and ls < b * 0.5 and _is_bullish(prev):
            patterns.append(
                {
                    "date": str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"]),
                    "pattern": "shooting_star",
                    "name_cn": "流星线",
                    "direction": "bearish",
                    "description": "上涨后出现流星线，看跌反转信号",
                }
            )
    return patterns


def detect_inverted_hammer(df: pd.DataFrame) -> list:
    """倒锤子: long upper shadow, small body at bottom, after downtrend."""
    patterns = []
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        b = _body(row)
        if b == 0:
            continue
        us = _upper_shadow(row)
        ls = _lower_shadow(row)
        if us >= 2 * b and ls < b * 0.5 and _is_bearish(prev):
            patterns.append(
                {
                    "date": str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"]),
                    "pattern": "inverted_hammer",
                    "name_cn": "倒锤子线",
                    "direction": "bullish",
                    "description": "下跌后出现倒锤子线，潜在看涨信号",
                }
            )
    return patterns


def detect_big_candle(df: pd.DataFrame) -> list:
    """大阳线/大阴线: body > 3% of open price."""
    patterns = []
    for i in range(len(df)):
        row = df.iloc[i]
        if row["open"] == 0:
            continue
        pct = abs(row["close"] - row["open"]) / row["open"] * 100
        if pct >= 3:
            is_bull = _is_bullish(row)
            patterns.append(
                {
                    "date": str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"]),
                    "pattern": "big_bullish_candle" if is_bull else "big_bearish_candle",
                    "name_cn": "大阳线" if is_bull else "大阴线",
                    "direction": "bullish" if is_bull else "bearish",
                    "description": f"{'大阳线' if is_bull else '大阴线'}，涨跌幅{pct:.1f}%，{'多方强势' if is_bull else '空方强势'}",
                }
            )
    return patterns


def detect_morning_star(df: pd.DataFrame) -> list:
    """启明星: bearish candle + small body + bullish candle closing above midpoint of first."""
    patterns = []
    for i in range(2, len(df)):
        c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
        b1, b2, _ = _body(c1), _body(c2), _body(c3)
        if b1 == 0:
            continue
        mid1 = (c1["open"] + c1["close"]) / 2
        if _is_bearish(c1) and b1 > b2 * 2 and _is_bullish(c3) and c3["close"] > mid1 and c2["close"] < c1["close"]:
            patterns.append(
                {
                    "date": str(c3["date"].date()) if hasattr(c3["date"], "date") else str(c3["date"]),
                    "pattern": "morning_star",
                    "name_cn": "启明星",
                    "direction": "bullish",
                    "description": "底部启明星形态，强看涨反转信号",
                }
            )
    return patterns


def detect_evening_star(df: pd.DataFrame) -> list:
    """黄昏星: bullish candle + small body + bearish candle closing below midpoint of first."""
    patterns = []
    for i in range(2, len(df)):
        c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
        b1, b2, _ = _body(c1), _body(c2), _body(c3)
        if b1 == 0:
            continue
        mid1 = (c1["open"] + c1["close"]) / 2
        if _is_bullish(c1) and b1 > b2 * 2 and _is_bearish(c3) and c3["close"] < mid1 and c2["close"] > c1["close"]:
            patterns.append(
                {
                    "date": str(c3["date"].date()) if hasattr(c3["date"], "date") else str(c3["date"]),
                    "pattern": "evening_star",
                    "name_cn": "黄昏星",
                    "direction": "bearish",
                    "description": "顶部黄昏星形态，强看跌反转信号",
                }
            )
    return patterns


def detect_bullish_engulfing(df: pd.DataFrame) -> list:
    """看涨吞没: bearish candle followed by larger bullish candle that engulfs it."""
    patterns = []
    for i in range(1, len(df)):
        prev, cur = df.iloc[i - 1], df.iloc[i]
        if _is_bearish(prev) and _is_bullish(cur) and cur["open"] <= prev["close"] and cur["close"] >= prev["open"]:
            patterns.append(
                {
                    "date": str(cur["date"].date()) if hasattr(cur["date"], "date") else str(cur["date"]),
                    "pattern": "bullish_engulfing",
                    "name_cn": "看涨吞没",
                    "direction": "bullish",
                    "description": "阳线完全包裹前一阴线，看涨反转信号",
                }
            )
    return patterns


def detect_bearish_engulfing(df: pd.DataFrame) -> list:
    """看跌吞没: bullish candle followed by larger bearish candle that engulfs it."""
    patterns = []
    for i in range(1, len(df)):
        prev, cur = df.iloc[i - 1], df.iloc[i]
        if _is_bullish(prev) and _is_bearish(cur) and cur["open"] >= prev["close"] and cur["close"] <= prev["open"]:
            patterns.append(
                {
                    "date": str(cur["date"].date()) if hasattr(cur["date"], "date") else str(cur["date"]),
                    "pattern": "bearish_engulfing",
                    "name_cn": "看跌吞没",
                    "direction": "bearish",
                    "description": "阴线完全包裹前一阳线，看跌反转信号",
                }
            )
    return patterns


def detect_double_bottom(df: pd.DataFrame) -> list:
    """双底 (W底): two lows within 2% tolerance separated by a peak."""
    patterns = []
    if len(df) < 20:
        return patterns
    lows = df["low"].values
    for i in range(10, len(df) - 2):
        window_left = lows[max(0, i - 10) : i]
        if len(window_left) == 0:
            continue
        left_min_idx = np.argmin(window_left) + max(0, i - 10)
        left_min = lows[left_min_idx]
        right_window = lows[i : min(len(lows), i + 10)]
        if len(right_window) < 3:
            continue
        right_min_idx = np.argmin(right_window) + i
        right_min = lows[right_min_idx]
        if left_min == 0:
            continue
        if abs(left_min - right_min) / left_min < 0.02:
            peak_between = max(df["high"].values[left_min_idx : right_min_idx + 1])
            if peak_between > left_min * 1.03:
                cur = df.iloc[right_min_idx]
                patterns.append(
                    {
                        "date": str(cur["date"].date()) if hasattr(cur["date"], "date") else str(cur["date"]),
                        "pattern": "double_bottom",
                        "name_cn": "双底（W底）",
                        "direction": "bullish",
                        "description": f"双底形态，两次低点{left_min:.2f}/{right_min:.2f}，颈线位{peak_between:.2f}",
                    }
                )
                break
    return patterns


def detect_breakout(df: pd.DataFrame) -> list:
    """20日突破: close breaks above 20-day high."""
    patterns = []
    if len(df) < 21:
        return patterns
    for i in range(20, len(df)):
        prev_high = df["high"].iloc[i - 20 : i].max()
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        if cur["close"] > prev_high and prev["close"] <= prev_high:
            patterns.append(
                {
                    "date": str(cur["date"].date()) if hasattr(cur["date"], "date") else str(cur["date"]),
                    "pattern": "breakout_20d",
                    "name_cn": "20日新高突破",
                    "direction": "bullish",
                    "description": f"收盘价突破20日高点{prev_high:.2f}，突破信号",
                }
            )
    return patterns


def detect_breakdown(df: pd.DataFrame) -> list:
    """20日跌破: close breaks below 20-day low."""
    patterns = []
    if len(df) < 21:
        return patterns
    for i in range(20, len(df)):
        prev_low = df["low"].iloc[i - 20 : i].min()
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        if cur["close"] < prev_low and prev["close"] >= prev_low:
            patterns.append(
                {
                    "date": str(cur["date"].date()) if hasattr(cur["date"], "date") else str(cur["date"]),
                    "pattern": "breakdown_20d",
                    "name_cn": "20日新低跌破",
                    "direction": "bearish",
                    "description": f"收盘价跌破20日低点{prev_low:.2f}，破位信号",
                }
            )
    return patterns


def detect_box_oscillation(df: pd.DataFrame) -> list:
    """箱体震荡: 10-day window with range < 8%."""
    patterns = []
    window = 10
    if len(df) < window + 1:
        return patterns
    for i in range(window, len(df)):
        w = df.iloc[i - window : i + 1]
        max_high = float(w["high"].max())
        min_low = float(w["low"].min())
        if min_low <= 0:
            continue
        range_pct = (max_high - min_low) / min_low * 100
        if range_pct < 8:
            cur = df.iloc[i]
            d = str(cur["date"].date()) if hasattr(cur["date"], "date") else str(cur["date"])
            if not patterns or patterns[-1]["date"] != d:
                patterns.append(
                    {
                        "date": d,
                        "pattern": "box_oscillation",
                        "name_cn": "箱体震荡",
                        "direction": "neutral",
                        "description": f"近{window}日振幅仅{range_pct:.1f}%，箱体区间{min_low:.2f}-{max_high:.2f}",
                        "box_high": round(max_high, 2),
                        "box_low": round(min_low, 2),
                        "box_range_pct": round(range_pct, 2),
                    }
                )
    return patterns


PATTERN_STRENGTH = {
    "doji": "弱",
    "inverted_hammer": "弱",
    "hammer": "中",
    "hanging_man": "中",
    "shooting_star": "中",
    "big_candle_up": "中",
    "big_candle_down": "中",
    "double_bottom": "中",
    "box_oscillation": "中",
    "morning_star": "强",
    "evening_star": "强",
    "bullish_engulfing": "强",
    "bearish_engulfing": "强",
    "breakout_20d": "强",
    "breakdown_20d": "强",
}


ALL_DETECTORS = [
    detect_doji,
    detect_hammer,
    detect_hanging_man,
    detect_shooting_star,
    detect_inverted_hammer,
    detect_big_candle,
    detect_morning_star,
    detect_evening_star,
    detect_bullish_engulfing,
    detect_bearish_engulfing,
    detect_double_bottom,
    detect_breakout,
    detect_breakdown,
    detect_box_oscillation,
]


def analyze(symbol: str, period: str = "daily", days: int = 60) -> dict:
    records = fetch_kline(symbol, period, days)
    if isinstance(records, dict) and "error" in records:
        return records
    if not records:
        return {"error": "No kline data returned"}

    df = to_dataframe(records)
    if len(df) < 5:
        return {"error": f"Insufficient data: {len(df)} rows (need at least 5)"}

    all_patterns = []
    for detector in ALL_DETECTORS:
        all_patterns.extend(detector(df))

    for p in all_patterns:
        p["strength"] = PATTERN_STRENGTH.get(p["pattern"], "中")

    all_patterns.sort(key=lambda p: p["date"], reverse=True)

    recent = (
        [
            p
            for p in all_patterns
            if p["date"]
            >= str(df["date"].iloc[-5].date() if hasattr(df["date"].iloc[-5], "date") else df["date"].iloc[-5])
        ]
        if len(df) >= 5
        else all_patterns
    )

    bullish = [p for p in recent if p["direction"] == "bullish"]
    bearish = [p for p in recent if p["direction"] == "bearish"]

    return {
        "symbol": symbol,
        "period": period,
        "data_points": len(df),
        "recent_patterns": recent[:20],
        "all_patterns_count": len(all_patterns),
        "recent_summary": {
            "bullish_count": len(bullish),
            "bearish_count": len(bearish),
            "neutral_count": len(recent) - len(bullish) - len(bearish),
            "bias": "bullish"
            if len(bullish) > len(bearish)
            else "bearish"
            if len(bearish) > len(bullish)
            else "neutral",
        },
        "all_patterns": all_patterns[-50:],
    }


def main():
    parser = argparse.ArgumentParser(description="K-line pattern recognition")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("analyze")
    p.add_argument("symbol")
    p.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly"])
    p.add_argument("--days", type=int, default=60)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = analyze(args.symbol, args.period, args.days)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
