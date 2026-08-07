#!/usr/bin/env python3
"""Volume-price analysis — correlation, up/down day ratios, trend, pattern interpretation."""

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
        encoding="utf-8",
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


def analyze_volume(symbol: str, period: str = "daily", count: int = 60) -> dict:
    records = fetch_kline(symbol, period, count)
    if isinstance(records, dict) and "error" in records:
        return records
    if not records:
        return {"error": "No kline data returned"}

    df = to_dataframe(records)
    if len(df) < 10:
        return {"error": f"Insufficient data: {len(df)} rows (need at least 10)"}

    close = df["close"]
    volume = df["volume"]
    price_change = close.pct_change().dropna()
    vol_change = volume.pct_change().dropna()

    min_len = min(len(price_change), len(vol_change))
    pc = price_change.tail(min_len).values
    vc = vol_change.tail(min_len).values
    mask = ~(np.isnan(pc) | np.isnan(vc))
    corr = float(np.corrcoef(pc[mask], vc[mask])[0, 1]) if mask.sum() > 2 else None

    up_days = df[close > close.shift(1)]
    down_days = df[close < close.shift(1)]
    avg_vol_up = float(up_days["volume"].mean()) if len(up_days) > 0 else 0
    avg_vol_down = float(down_days["volume"].mean()) if len(down_days) > 0 else 0
    up_down_ratio = round(avg_vol_up / avg_vol_down, 2) if avg_vol_down > 0 else None

    vol_ma5 = volume.rolling(5).mean()
    vol_ma20 = volume.rolling(20).mean()
    cur_vol = float(volume.iloc[-1])
    vol_ratio_5 = round(cur_vol / float(vol_ma5.iloc[-1]), 2) if float(vol_ma5.iloc[-1]) > 0 else None
    vol_ratio_20 = (
        round(cur_vol / float(vol_ma20.iloc[-1]), 2)
        if len(vol_ma20.dropna()) > 0 and float(vol_ma20.iloc[-1]) > 0
        else None
    )

    recent_vol = volume.tail(5).mean()
    prior_vol = volume.tail(20).head(15).mean()
    if prior_vol > 0:
        vol_trend_ratio = float(recent_vol / prior_vol)
        if vol_trend_ratio > 1.5:
            vol_trend = "increasing"
        elif vol_trend_ratio < 0.7:
            vol_trend = "decreasing"
        else:
            vol_trend = "stable"
    else:
        vol_trend = "unknown"
        vol_trend_ratio = None

    patterns = []
    latest_price_up = float(close.iloc[-1]) > float(close.iloc[-2]) if len(close) >= 2 else None
    latest_heavy = vol_ratio_5 and vol_ratio_5 > 1.5

    if latest_heavy and latest_price_up:
        patterns.append(
            {"type": "heavy_volume_rally", "signal": "bullish", "desc": "放量上涨 — 资金积极入场，短期看多"}
        )
    elif latest_heavy and latest_price_up is False:
        patterns.append({"type": "heavy_volume_decline", "signal": "bearish", "desc": "放量下跌 — 资金出逃，短期看空"})
    elif vol_ratio_5 and vol_ratio_5 < 0.6 and latest_price_up is False:
        patterns.append(
            {"type": "shrink_volume_decline", "signal": "neutral_to_bullish", "desc": "缩量回调 — 抛压减轻，可能蓄势"}
        )
    elif vol_ratio_5 and vol_ratio_5 < 0.6 and latest_price_up:
        patterns.append(
            {"type": "shrink_volume_rally", "signal": "weak_bullish", "desc": "缩量上涨 — 追高意愿不强，持续性存疑"}
        )

    if up_down_ratio and up_down_ratio > 1.3:
        patterns.append(
            {
                "type": "bullish_volume_bias",
                "signal": "bullish",
                "desc": f"上涨日均量远高于下跌日(比值{up_down_ratio})，资金偏多",
            }
        )
    elif up_down_ratio and up_down_ratio < 0.7:
        patterns.append(
            {
                "type": "bearish_volume_bias",
                "signal": "bearish",
                "desc": f"下跌日均量高于上涨日(比值{up_down_ratio})，资金偏空",
            }
        )

    if corr is not None and corr > 0.5:
        patterns.append(
            {"type": "positive_vol_price_corr", "signal": "healthy", "desc": f"量价正相关({corr:.2f})，价量配合良好"}
        )
    elif corr is not None and corr < -0.3:
        patterns.append(
            {"type": "vol_price_divergence", "signal": "warning", "desc": f"量价背离({corr:.2f})，关注趋势转折"}
        )

    high_vol_mask = volume > vol_ma20 * 2
    high_volume_days = int(high_vol_mask.tail(20).sum()) if len(vol_ma20.dropna()) > 0 else 0

    return {
        "symbol": symbol,
        "period": period,
        "data_points": len(df),
        "current_volume": int(cur_vol),
        "vol_ratio_5d": vol_ratio_5,
        "vol_ratio_20d": vol_ratio_20,
        "avg_volume_up_days": round(avg_vol_up) if avg_vol_up else None,
        "avg_volume_down_days": round(avg_vol_down) if avg_vol_down else None,
        "up_down_volume_ratio": up_down_ratio,
        "vol_price_correlation": round(corr, 3) if corr is not None else None,
        "volume_trend": vol_trend,
        "volume_trend_ratio": round(vol_trend_ratio, 2) if vol_trend_ratio else None,
        "high_volume_days_20d": high_volume_days,
        "patterns": patterns,
    }


def main():
    parser = argparse.ArgumentParser(description="Volume-price analysis")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("analyze")
    p.add_argument("symbol")
    p.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly"])
    p.add_argument("--count", type=int, default=60)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = analyze_volume(args.symbol, args.period, args.count)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
