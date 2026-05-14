#!/usr/bin/env python3
"""Technical indicator analysis — MA, MACD, RSI, BOLL, KDJ, volume."""

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd


def fetch_kline(symbol: str, period: str = "daily", count: int = 120) -> list:
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_data.py")
    r = subprocess.run(
        [sys.executable, script, "kline", symbol, "--period", period, "--count", str(count)],
        capture_output=True, text=True,
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


# --------------- Indicators ---------------

def calc_ma(close: pd.Series) -> dict:
    mas = {}
    for n in [5, 10, 20, 60]:
        ma = close.rolling(n).mean()
        mas[f"ma{n}"] = round(float(ma.iloc[-1]), 2) if len(ma.dropna()) > 0 else None
    vals = [mas.get(f"ma{n}") for n in [5, 10, 20, 60]]
    valid = [v for v in vals if v is not None]
    if len(valid) >= 3:
        if valid == sorted(valid, reverse=True):
            mas["ma_arrangement"] = "bullish"
        elif valid == sorted(valid):
            mas["ma_arrangement"] = "bearish"
        else:
            mas["ma_arrangement"] = "mixed"
    else:
        mas["ma_arrangement"] = "insufficient_data"
    return mas


def calc_macd(close: pd.Series) -> dict:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = 2 * (dif - dea)
    d, de, m = float(dif.iloc[-1]), float(dea.iloc[-1]), float(macd.iloc[-1])
    signal = "bullish" if d > de else "bearish"
    prev_cross = "golden_cross" if len(dif) >= 2 and dif.iloc[-2] <= dea.iloc[-2] and d > de else \
                 "death_cross" if len(dif) >= 2 and dif.iloc[-2] >= dea.iloc[-2] and d < de else None
    result = {"dif": round(d, 2), "dea": round(de, 2), "macd": round(m, 2), "signal": signal}
    if prev_cross:
        result["cross"] = prev_cross
    return result


def calc_rsi(close: pd.Series) -> dict:
    result = {}
    for n in [6, 12, 24]:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(n).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(n).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)
        val = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None
        result[f"rsi{n}"] = round(val, 2) if val is not None else None

    r6 = result.get("rsi6")
    if r6 is not None:
        if r6 > 80:
            result["signal"] = "overbought"
        elif r6 < 20:
            result["signal"] = "oversold"
        elif r6 > 70:
            result["signal"] = "approaching_overbought"
        elif r6 < 30:
            result["signal"] = "approaching_oversold"
        else:
            result["signal"] = "neutral"
    else:
        result["signal"] = "insufficient_data"
    return result


def calc_bollinger(close: pd.Series) -> dict:
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    u, m, l_ = float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1])
    price = float(close.iloc[-1])
    bw = (u - l_) / m * 100 if m != 0 else 0
    pos = (price - l_) / (u - l_) if (u - l_) != 0 else 0.5
    if pos > 0.8:
        position = "near_upper"
    elif pos < 0.2:
        position = "near_lower"
    elif pos > 0.5:
        position = "upper_half"
    else:
        position = "lower_half"
    return {"upper": round(u, 2), "mid": round(m, 2), "lower": round(l_, 2),
            "position": position, "bandwidth": round(bw, 2)}


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series) -> dict:
    low_n = low.rolling(9).min()
    high_n = high.rolling(9).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    kv, dv, jv = float(k.iloc[-1]), float(d.iloc[-1]), float(j.iloc[-1])
    if jv > 80:
        signal = "overbought"
    elif jv < 20:
        signal = "oversold"
    else:
        signal = "neutral"
    return {"k": round(kv, 2), "d": round(dv, 2), "j": round(jv, 2), "signal": signal}


def calc_volume(volume: pd.Series) -> dict:
    vol_ma5 = float(volume.rolling(5).mean().iloc[-1])
    vol_ma10 = float(volume.rolling(10).mean().iloc[-1])
    cur = float(volume.iloc[-1])
    ratio = cur / vol_ma5 if vol_ma5 > 0 else 0
    if ratio > 3:
        signal = "extremely_heavy"
    elif ratio > 2:
        signal = "heavy"
    elif ratio < 0.5:
        signal = "light"
    else:
        signal = "normal"
    return {"current": int(cur), "vol_ma5": round(vol_ma5, 0),
            "vol_ma10": round(vol_ma10, 0), "volume_ratio": round(ratio, 2),
            "signal": signal}


def calc_support_resistance(close: pd.Series, high: pd.Series, low: pd.Series, boll: dict, ma: dict) -> dict:
    recent_high = round(float(high.tail(20).max()), 2)
    recent_low = round(float(low.tail(20).min()), 2)
    resistance = sorted(set(filter(None, [boll.get("upper"), recent_high, ma.get("ma60")])), reverse=True)[:3]
    support = sorted(set(filter(None, [boll.get("lower"), recent_low, ma.get("ma60")])))[:3]
    return {"resistance": resistance, "support": support}


def calc_trend(ma: dict) -> dict:
    def _cmp(a, b):
        if a is None or b is None:
            return "unknown"
        return "bullish" if a > b else "bearish" if a < b else "neutral"

    short = _cmp(ma.get("ma5"), ma.get("ma10"))
    medium = _cmp(ma.get("ma10"), ma.get("ma20"))
    long_ = _cmp(ma.get("ma20"), ma.get("ma60"))
    scores = {"bullish": 1, "neutral": 0, "bearish": -1, "unknown": 0}
    total = scores[short] + scores[medium] + scores[long_]
    overall = "bullish" if total >= 2 else "bearish" if total <= -2 else "neutral"
    return {"short_term": short, "medium_term": medium, "long_term": long_, "overall": overall}


def generate_signals(ma_data: dict, macd_data: dict, rsi_data: dict,
                     boll_data: dict, kdj_data: dict, vol_data: dict, trend_data: dict) -> list:
    signals = []
    if macd_data.get("cross") == "golden_cross":
        signals.append("MACD金叉，短期看多")
    elif macd_data.get("cross") == "death_cross":
        signals.append("MACD死叉，短期看空")

    if macd_data["signal"] == "bullish":
        signals.append("MACD多头排列（DIF > DEA）")
    else:
        signals.append("MACD空头排列（DIF < DEA）")

    rsi_sig = rsi_data.get("signal", "")
    if "overbought" in rsi_sig:
        signals.append(f"RSI超买区间（RSI6={rsi_data.get('rsi6')}），注意回调风险")
    elif "oversold" in rsi_sig:
        signals.append(f"RSI超卖区间（RSI6={rsi_data.get('rsi6')}），关注反弹机会")
    else:
        signals.append(f"RSI处于中性区间（RSI6={rsi_data.get('rsi6')}）")

    bp = boll_data.get("position", "")
    if bp == "near_upper":
        signals.append("股价接近布林带上轨，短期压力较大")
    elif bp == "near_lower":
        signals.append("股价接近布林带下轨，可能存在支撑")
    elif bp == "upper_half":
        signals.append("股价运行于布林带上半区，偏强势")
    else:
        signals.append("股价运行于布林带下半区，偏弱势")

    if kdj_data["signal"] == "overbought":
        signals.append(f"KDJ超买（J={kdj_data['j']}），注意高位风险")
    elif kdj_data["signal"] == "oversold":
        signals.append(f"KDJ超卖（J={kdj_data['j']}），关注低位机会")

    vs = vol_data.get("signal", "")
    if "heavy" in vs:
        signals.append(f"成交量放大（量比={vol_data['volume_ratio']}），市场关注度高")
    elif vs == "light":
        signals.append(f"缩量交易（量比={vol_data['volume_ratio']}），市场观望情绪浓")

    arr = ma_data.get("ma_arrangement")
    if arr == "bullish":
        signals.append("均线多头排列，趋势向好")
    elif arr == "bearish":
        signals.append("均线空头排列，趋势偏弱")

    return signals


# --------------- Main ---------------

def analyze(symbol: str, period: str = "daily", count: int = 120) -> dict:
    records = fetch_kline(symbol, period, count)
    if isinstance(records, dict) and "error" in records:
        return records
    if not records:
        return {"error": "No kline data returned"}

    df = to_dataframe(records)
    if len(df) < 30:
        return {"error": f"Insufficient data: {len(df)} rows (need at least 30)"}

    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    ma_data = calc_ma(close)
    macd_data = calc_macd(close)
    rsi_data = calc_rsi(close)
    boll_data = calc_bollinger(close)
    kdj_data = calc_kdj(high, low, close)
    vol_data = calc_volume(volume)
    sr_data = calc_support_resistance(close, high, low, boll_data, ma_data)
    trend_data = calc_trend(ma_data)
    sig_list = generate_signals(ma_data, macd_data, rsi_data, boll_data, kdj_data, vol_data, trend_data)

    return {
        "symbol": symbol,
        "period": period,
        "data_points": len(df),
        "latest": {
            "date": str(df["date"].iloc[-1].date()) if hasattr(df["date"].iloc[-1], "date") else str(df["date"].iloc[-1]),
            "close": round(float(close.iloc[-1]), 2),
            "volume": int(volume.iloc[-1]),
        },
        "moving_averages": ma_data,
        "macd": macd_data,
        "rsi": rsi_data,
        "bollinger": boll_data,
        "kdj": kdj_data,
        "volume": vol_data,
        "support_resistance": sr_data,
        "trend": trend_data,
        "signals": sig_list,
    }


def main():
    parser = argparse.ArgumentParser(description="Technical indicator analysis")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("analyze")
    p.add_argument("symbol")
    p.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly"])
    p.add_argument("--count", type=int, default=120)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = analyze(args.symbol, args.period, args.count)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
