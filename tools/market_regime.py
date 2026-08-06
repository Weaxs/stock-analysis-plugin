#!/usr/bin/env python3
"""Market regime detection — classify market phase to guide strategy selection."""

import argparse
import json
import os
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _run_tool(script: str, args: str) -> dict | list:
    cmd = f"python3 {TOOLS_DIR}/{script} {args}"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return {}


INDEX_MAP = {
    "A": ("000001", "上证综指"),
    "HK": ("HSI", "恒生指数"),
    "US": ("^GSPC", "标普500"),
}

REGIME_CN = {
    "trending_up": "上涨趋势",
    "trending_down": "下跌趋势",
    "sideways": "横盘震荡",
    "volatile": "高波动",
    "mixed": "混合状态",
}

SKILL_RECOMMEND = {
    "trending_up": {
        "recommend": ["bull-trend", "volume-breakout", "ma-crossover", "dragon-head"],
        "avoid": ["bottom-volume"],
    },
    "trending_down": {"recommend": ["shrink-pullback", "bottom-volume"], "avoid": ["bull-trend", "volume-breakout"]},
    "sideways": {"recommend": ["box-oscillation", "shrink-pullback"], "avoid": ["dragon-head"]},
    "volatile": {"recommend": ["chan-theory", "wave-theory", "emotion-cycle"], "avoid": ["bull-trend"]},
    "mixed": {"recommend": ["shrink-pullback", "chan-theory"], "avoid": []},
}


def _get_index_kline(market: str) -> list:
    code, _ = INDEX_MAP[market]
    if market == "A":
        data = _run_tool("stock_data.py", f"kline {code} --period daily --count 80")
    else:
        data = _run_tool("stock_data.py", f"kline {code} --period daily --count 80")
    if isinstance(data, list) and len(data) > 0:
        return data
    return []


def _compute_indicators(klines: list) -> dict:
    import numpy as np

    closes = np.array([float(k["close"]) for k in klines if k.get("close") is not None])
    if len(closes) < 20:
        return {}

    def ma(n):
        return np.mean(closes[-n:]) if len(closes) >= n else np.mean(closes)

    ma5 = ma(5)
    ma10 = ma(10)
    ma20 = ma(20)
    ma60 = ma(60) if len(closes) >= 60 else ma(len(closes))
    close = closes[-1]

    highs = np.array([float(k["high"]) for k in klines if k.get("high") is not None])
    lows = np.array([float(k["low"]) for k in klines if k.get("low") is not None])

    tr_values = []
    for i in range(-min(14, len(closes) - 1), 0):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_values.append(tr)
    atr = np.mean(tr_values) if tr_values else 0
    atr_ratio = atr / close if close > 0 else 0

    upper = ma20 + 2 * np.std(closes[-20:])
    lower = ma20 - 2 * np.std(closes[-20:])
    boll_width = (upper - lower) / ma20 if ma20 > 0 else 0

    gains, losses = [], []
    for i in range(1, min(15, len(closes))):
        diff = closes[-i] - closes[-i - 1]
        if diff > 0:
            gains.append(diff)
        else:
            losses.append(abs(diff))
    avg_gain = np.mean(gains) if gains else 0
    avg_loss = np.mean(losses) if losses else 1
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    rsi14 = 100 - (100 / (1 + rs))

    ema12 = np.mean(closes[-12:])
    ema26 = np.mean(closes[-26:]) if len(closes) >= 26 else np.mean(closes)
    macd_diff = ema12 - ema26
    macd_trend = "bullish" if macd_diff > 0 else "bearish"

    ma_spread = abs(ma5 - ma20) / ma20 if ma20 > 0 else 0

    prev_close = closes[-2] if len(closes) >= 2 else close
    change_pct = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0

    return {
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "atr_ratio": round(atr_ratio, 4),
        "boll_width": round(boll_width, 4),
        "rsi14": round(rsi14, 1),
        "macd_trend": macd_trend,
        "ma_spread_pct": round(ma_spread * 100, 2),
    }


def classify_regime(ind: dict) -> tuple:
    ma5 = ind["ma5"]
    ma10 = ind["ma10"]
    ma20 = ind["ma20"]
    ma60 = ind["ma60"]
    close = ind["close"]
    atr_ratio = ind["atr_ratio"]
    boll_width = ind["boll_width"]
    ma_spread = ind["ma_spread_pct"] / 100

    bullish_ma = ma5 > ma10 > ma20
    bearish_ma = ma5 < ma10 < ma20
    above_ma60 = close > ma60
    high_volatility = atr_ratio > 0.025 or boll_width > 0.15
    strong_trend = ma_spread > 0.03

    if bullish_ma and above_ma60 and strong_trend:
        regime = "trending_up"
        confidence = 0.85 + (0.1 if ma_spread > 0.05 else 0)
    elif bearish_ma and not above_ma60 and strong_trend:
        regime = "trending_down"
        confidence = 0.85 + (0.1 if ma_spread > 0.05 else 0)
    elif high_volatility and not strong_trend:
        regime = "volatile"
        confidence = 0.7
    elif not strong_trend and not high_volatility:
        regime = "sideways"
        confidence = 0.75
    else:
        regime = "mixed"
        confidence = 0.5

    ma_arrangement = "bullish" if bullish_ma else ("bearish" if bearish_ma else "mixed")
    return regime, min(confidence, 0.95), ma_arrangement


def _detect_sector_heat(market: str) -> bool:
    if market != "A":
        return False
    data = _run_tool("stock_data.py", "sector_rankings --top 5 --direction both")
    if not isinstance(data, dict):
        return False
    top = data.get("top", [])
    bottom = data.get("bottom", [])
    if not top or not bottom:
        return False
    try:
        top_pct = float(top[0].get("change_pct", 0))
        bottom_pct = float(bottom[0].get("change_pct", 0))
        return top_pct > 3 and bottom_pct < -1
    except (ValueError, TypeError, IndexError):
        return False


def detect_regime(market: str = "A") -> dict:
    market = market.upper()
    if market not in INDEX_MAP:
        return {"error": f"Unsupported market: {market}. Use A/HK/US"}

    code, name = INDEX_MAP[market]
    klines = _get_index_kline(market)
    if not klines:
        return {"error": f"Failed to fetch index data for {name}"}

    ind = _compute_indicators(klines)
    if not ind:
        return {"error": "Insufficient data to compute indicators"}

    regime, confidence, ma_arrangement = classify_regime(ind)
    sector_heat = _detect_sector_heat(market)

    skills = SKILL_RECOMMEND.get(regime, {"recommend": [], "avoid": []})
    recommended = list(skills["recommend"])
    avoid = list(skills["avoid"])
    if sector_heat:
        if "dragon-head" not in recommended:
            recommended.append("dragon-head")
        if "emotion-cycle" not in recommended:
            recommended.append("emotion-cycle")

    return {
        "market": market,
        "index": {"code": code, "name": name, "close": ind["close"], "change_pct": ind["change_pct"]},
        "regime": regime,
        "regime_cn": REGIME_CN.get(regime, regime),
        "confidence": round(confidence, 2),
        "indicators": {
            "ma_arrangement": ma_arrangement,
            "above_ma60": ind["close"] > ind["ma60"],
            "ma_spread_pct": ind["ma_spread_pct"],
            "atr_ratio": ind["atr_ratio"],
            "boll_width": ind["boll_width"],
            "rsi14": ind["rsi14"],
            "macd_trend": ind["macd_trend"],
        },
        "sector_heat": sector_heat,
        "recommended_skills": recommended,
        "avoid_skills": avoid,
    }


def main():
    parser = argparse.ArgumentParser(description="Market regime detection")
    sub = parser.add_subparsers(dest="command")

    p_detect = sub.add_parser("detect")
    p_detect.add_argument("market", nargs="?", default="A", choices=["A", "HK", "US"])

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "detect":
        result = detect_regime(args.market)
        print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
