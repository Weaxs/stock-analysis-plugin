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
    prev_cross = (
        "golden_cross"
        if len(dif) >= 2 and dif.iloc[-2] <= dea.iloc[-2] and d > de
        else "death_cross"
        if len(dif) >= 2 and dif.iloc[-2] >= dea.iloc[-2] and d < de
        else None
    )
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
    return {
        "upper": round(u, 2),
        "mid": round(m, 2),
        "lower": round(l_, 2),
        "position": position,
        "bandwidth": round(bw, 2),
    }


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
    return {
        "current": int(cur),
        "vol_ma5": round(vol_ma5, 0),
        "vol_ma10": round(vol_ma10, 0),
        "volume_ratio": round(ratio, 2),
        "signal": signal,
    }


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


def calc_bias(close: pd.Series, ma_data: dict) -> dict:
    price = float(close.iloc[-1])
    result = {}
    for n in [5, 10, 20]:
        ma_val = ma_data.get(f"ma{n}")
        if ma_val and ma_val > 0:
            result[f"bias_ma{n}"] = round((price - ma_val) / ma_val * 100, 2)
        else:
            result[f"bias_ma{n}"] = None
    return result


def calc_volume_direction(close: pd.Series, volume: pd.Series) -> str:
    if len(close) < 2:
        return "normal"
    price_up = float(close.iloc[-1]) > float(close.iloc[-2])
    vol_ma5 = float(volume.rolling(5).mean().iloc[-1])
    cur_vol = float(volume.iloc[-1])
    heavy = cur_vol > vol_ma5 * 1.5
    shrink = cur_vol < vol_ma5 * 0.7
    if heavy and price_up:
        return "heavy_volume_up"
    elif heavy and not price_up:
        return "heavy_volume_down"
    elif shrink and not price_up:
        return "shrink_volume_down"
    elif shrink and price_up:
        return "shrink_volume_up"
    return "normal"


def calc_ma_support(close: pd.Series, ma_data: dict) -> dict:
    price = float(close.iloc[-1])
    tolerance = 0.02
    support_ma5 = False
    support_ma10 = False
    if ma_data.get("ma5"):
        diff = (price - ma_data["ma5"]) / ma_data["ma5"]
        if -tolerance <= diff <= tolerance * 2:
            support_ma5 = True
    if ma_data.get("ma10"):
        diff = (price - ma_data["ma10"]) / ma_data["ma10"]
        if -tolerance <= diff <= tolerance * 2:
            support_ma10 = True
    return {"support_ma5": support_ma5, "support_ma10": support_ma10}


def generate_signal_score(
    ma_data: dict,
    macd_data: dict,
    rsi_data: dict,
    vol_data: dict,
    trend_data: dict,
    bias_data: dict,
    close: pd.Series,
    volume: pd.Series,
) -> dict:
    score = 0
    reasons = []
    risks = []

    # --- Trend score (30 points) ---
    arrangement = ma_data.get("ma_arrangement", "mixed")
    overall = trend_data.get("overall", "neutral")
    short_t = trend_data.get("short_term", "neutral")
    medium_t = trend_data.get("medium_term", "neutral")
    long_t = trend_data.get("long_term", "neutral")

    bull_count = sum(1 for t in [short_t, medium_t, long_t] if t == "bullish")
    bear_count = sum(1 for t in [short_t, medium_t, long_t] if t == "bearish")

    if arrangement == "bullish" and bull_count == 3:
        trend_score = 30
        reasons.append("均线完美多头排列，趋势强劲")
    elif arrangement == "bullish":
        trend_score = 26
        reasons.append("均线多头排列，趋势向好")
    elif bull_count >= 2:
        trend_score = 18
        reasons.append("短中期趋势偏多")
    elif arrangement == "mixed":
        trend_score = 12
    elif bear_count >= 2:
        trend_score = 8
        risks.append("中长期趋势偏空")
    elif arrangement == "bearish" and bear_count == 3:
        trend_score = 0
        risks.append("均线空头排列，趋势极弱")
    elif arrangement == "bearish":
        trend_score = 4
        risks.append("均线空头排列，不宜做多")
    else:
        trend_score = 12
    score += trend_score

    # --- Bias score (20 points) ---
    is_strong_bull = (
        arrangement == "bullish"
        and bull_count == 3
        and trend_score >= 21
        and ma_data.get("ma5")
        and ma_data.get("ma20")
        and ma_data["ma20"] > 0
        and (ma_data["ma5"] - ma_data["ma20"]) / ma_data["ma20"] > 0.05
    )
    bias_threshold = 7.5 if is_strong_bull else 5.0

    bias5 = bias_data.get("bias_ma5")
    if bias5 is not None:
        if bias5 < 0:
            if bias5 > -3:
                score += 20
                reasons.append(f"价格略低于MA5({bias5:.1f}%)，回踩买点")
            elif bias5 > -5:
                score += 16
                reasons.append(f"价格回踩MA5({bias5:.1f}%)，观察支撑")
            else:
                score += 8
                risks.append(f"乖离率过大({bias5:.1f}%)，可能破位")
        elif bias5 < 2:
            score += 18
            reasons.append(f"价格贴近MA5({bias5:.1f}%)，介入好时机")
        elif bias5 < 5:
            score += 14
            reasons.append(f"价格略高于MA5({bias5:.1f}%)，可小仓介入")
        elif is_strong_bull and bias5 < bias_threshold:
            score += 10
            reasons.append(f"强势趋势下乖离({bias5:.1f}%)尚可，可轻仓追踪")
        else:
            score += 4
            risks.append(f"乖离率过高({bias5:.1f}%)，严禁追高")
    else:
        score += 10

    # --- Volume score (15 points) ---
    vol_dir = calc_volume_direction(close, volume)
    vol_scores = {
        "shrink_volume_down": 15,
        "heavy_volume_up": 12,
        "normal": 10,
        "shrink_volume_up": 6,
        "heavy_volume_down": 0,
    }
    vol_score = vol_scores.get(vol_dir, 8)
    score += vol_score
    if vol_dir == "shrink_volume_down":
        reasons.append("缩量回调，主力洗盘特征")
    elif vol_dir == "heavy_volume_down":
        risks.append("放量下跌，注意风险")
    elif vol_dir == "heavy_volume_up":
        reasons.append("放量上涨，资金积极入场")

    # --- Support score (10 points) ---
    ma_support = calc_ma_support(close, ma_data)
    if ma_support["support_ma5"]:
        score += 5
        reasons.append("MA5支撑有效")
    if ma_support["support_ma10"]:
        score += 5
        reasons.append("MA10支撑有效")

    # --- MACD score (15 points) ---
    cross = macd_data.get("cross")
    macd_sig = macd_data.get("signal", "bearish")
    dif = macd_data.get("dif", 0)

    if cross == "golden_cross" and dif > 0:
        macd_score = 15
        reasons.append("MACD零轴上金叉，强势信号")
    elif cross == "golden_cross":
        macd_score = 12
        reasons.append("MACD金叉，短期看多")
    elif macd_sig == "bullish" and dif > 0:
        macd_score = 10
        reasons.append("MACD多头且位于零轴上方")
    elif macd_sig == "bullish":
        macd_score = 8
    elif cross == "death_cross":
        macd_score = 0
        risks.append("MACD死叉，短期看空")
    elif macd_sig == "bearish":
        macd_score = 2
    else:
        macd_score = 5
    score += macd_score

    # --- RSI score (10 points) ---
    rsi_sig = rsi_data.get("signal", "neutral")
    rsi12 = rsi_data.get("rsi12")
    if rsi_sig == "oversold" or rsi_sig == "approaching_oversold":
        rsi_score = 10
        reasons.append(f"RSI超卖区间({rsi12})，反弹机会大")
    elif rsi_sig == "neutral" and rsi12 and rsi12 < 50:
        rsi_score = 8
    elif rsi_sig == "neutral":
        rsi_score = 5
    elif rsi_sig == "approaching_overbought":
        rsi_score = 3
        risks.append(f"RSI接近超买({rsi12})，注意回调")
    elif rsi_sig == "overbought":
        rsi_score = 0
        risks.append(f"RSI超买({rsi12})，回调风险高")
    else:
        rsi_score = 5
    score += rsi_score

    # --- Buy signal classification ---
    if score >= 75 and overall == "bullish":
        buy_signal = "STRONG_BUY"
    elif score >= 60 and overall in ("bullish", "neutral"):
        buy_signal = "BUY"
    elif score >= 45:
        buy_signal = "HOLD"
    elif score >= 30:
        buy_signal = "WAIT"
    elif overall == "bearish" and score < 20:
        buy_signal = "STRONG_SELL"
    else:
        buy_signal = "SELL"

    return {
        "signal_score": score,
        "buy_signal": buy_signal,
        "signal_reasons": reasons,
        "risk_factors": risks,
    }


def generate_signals(
    ma_data: dict, macd_data: dict, rsi_data: dict, boll_data: dict, kdj_data: dict, vol_data: dict, trend_data: dict
) -> list:
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


# --------------- Standalone MA calculator ---------------


def calculate_ma_standalone(
    symbol: str, periods: list[int] = None, kline_period: str = "daily", count: int = 120
) -> dict:
    if periods is None:
        periods = [5, 10, 20, 30, 60, 120, 250]

    records = fetch_kline(symbol, kline_period, count)
    if isinstance(records, dict) and "error" in records:
        return records
    if not records:
        return {"error": "No kline data returned"}

    df = to_dataframe(records)
    close = df["close"]
    price = float(close.iloc[-1])

    mas = {}
    for n in periods:
        if len(close) < n:
            mas[f"ma{n}"] = None
            continue
        val = float(close.rolling(n).mean().iloc[-1])
        mas[f"ma{n}"] = round(val, 2)

    bias = {}
    for n in periods:
        ma_val = mas.get(f"ma{n}")
        if ma_val and ma_val > 0:
            bias[f"bias_ma{n}"] = round((price - ma_val) / ma_val * 100, 2)
        else:
            bias[f"bias_ma{n}"] = None

    valid_mas = [(n, mas[f"ma{n}"]) for n in periods if mas.get(f"ma{n}") is not None]
    if len(valid_mas) >= 3:
        vals = [v for _, v in valid_mas]
        if vals == sorted(vals, reverse=True):
            alignment = "bullish"
        elif vals == sorted(vals):
            alignment = "bearish"
        else:
            alignment = "mixed"
    else:
        alignment = "insufficient_data"

    cross_signals = []
    for fast, slow in [(5, 10), (5, 20), (10, 20), (20, 60)]:
        if fast not in periods or slow not in periods:
            continue
        if len(close) < max(fast, slow) + 1:
            continue
        ma_fast = close.rolling(fast).mean()
        ma_slow = close.rolling(slow).mean()
        if len(ma_fast.dropna()) >= 2 and len(ma_slow.dropna()) >= 2:
            prev_diff = float(ma_fast.iloc[-2]) - float(ma_slow.iloc[-2])
            curr_diff = float(ma_fast.iloc[-1]) - float(ma_slow.iloc[-1])
            if prev_diff <= 0 and curr_diff > 0:
                cross_signals.append({"type": "golden_cross", "fast": f"MA{fast}", "slow": f"MA{slow}"})
            elif prev_diff >= 0 and curr_diff < 0:
                cross_signals.append({"type": "death_cross", "fast": f"MA{fast}", "slow": f"MA{slow}"})

    return {
        "symbol": symbol,
        "period": kline_period,
        "price": price,
        "moving_averages": mas,
        "bias": bias,
        "alignment": alignment,
        "cross_signals": cross_signals,
    }


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
    bias_data = calc_bias(close, ma_data)
    sig_list = generate_signals(ma_data, macd_data, rsi_data, boll_data, kdj_data, vol_data, trend_data)
    score_data = generate_signal_score(ma_data, macd_data, rsi_data, vol_data, trend_data, bias_data, close, volume)

    return {
        "symbol": symbol,
        "period": period,
        "data_points": len(df),
        "latest": {
            "date": str(df["date"].iloc[-1].date())
            if hasattr(df["date"].iloc[-1], "date")
            else str(df["date"].iloc[-1]),
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
        "bias": bias_data,
        "signal_score": score_data["signal_score"],
        "buy_signal": score_data["buy_signal"],
        "signal_reasons": score_data["signal_reasons"],
        "risk_factors": score_data["risk_factors"],
        "signals": sig_list,
    }


def main():
    parser = argparse.ArgumentParser(description="Technical indicator analysis")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("analyze")
    p.add_argument("symbol")
    p.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly"])
    p.add_argument("--count", type=int, default=120)

    p_ma = sub.add_parser("calculate_ma")
    p_ma.add_argument("symbol")
    p_ma.add_argument("--periods", default="5,10,20,30,60,120,250", help="Comma-separated MA periods")
    p_ma.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly"])
    p_ma.add_argument("--count", type=int, default=250)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "analyze":
        result = analyze(args.symbol, args.period, args.count)
    elif args.command == "calculate_ma":
        periods = [int(x.strip()) for x in args.periods.split(",")]
        result = calculate_ma_standalone(args.symbol, periods, args.period, args.count)
    else:
        result = {"error": f"Unknown command: {args.command}"}
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
