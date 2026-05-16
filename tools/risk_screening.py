#!/usr/bin/env python3
"""Risk screening — 7-dimension risk checker for A-share stocks."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta


TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _run_tool(script: str, args: str) -> dict:
    cmd = f"python3 {TOOLS_DIR}/{script} {args}"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return {}


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return v if v == v else None  # NaN check
    except (ValueError, TypeError):
        return None


# --------------- Check 1: Valuation ---------------

def check_valuation(symbol: str) -> dict:
    quote = _run_tool("stock_data.py", f"quote {symbol}")
    pe = _safe_float(quote.get("pe"))
    pb = _safe_float(quote.get("pb"))
    result = {"pe": pe, "pb": pb, "status": "normal", "flags": []}

    if pe is not None:
        if pe < 0:
            result["flags"].append({"category": "valuation", "severity": "medium",
                                     "description": "PE为负，公司亏损", "source": "data"})
            result["status"] = "warning"
        elif pe > 200:
            result["flags"].append({"category": "valuation", "severity": "high",
                                     "description": f"PE={pe:.1f}，估值极端偏高", "source": "data"})
            result["status"] = "extreme"
        elif pe > 100:
            result["flags"].append({"category": "valuation", "severity": "medium",
                                     "description": f"PE={pe:.1f}，估值偏高", "source": "data"})
            result["status"] = "warning"

    if pb is not None:
        if pb > 15:
            result["flags"].append({"category": "valuation", "severity": "high",
                                     "description": f"PB={pb:.1f}，市净率极端", "source": "data"})
            result["status"] = "extreme"
        elif pb > 10:
            result["flags"].append({"category": "valuation", "severity": "medium",
                                     "description": f"PB={pb:.1f}，市净率偏高", "source": "data"})
            if result["status"] == "normal":
                result["status"] = "warning"

    return result


# --------------- Check 2: Technical Warning ---------------

def check_technical(symbol: str) -> dict:
    ta = _run_tool("technical.py", f"analyze {symbol}")
    result = {"flags": []}

    if not ta or "error" in ta:
        return result

    signal = ta.get("buy_signal", "")
    score = _safe_float(ta.get("signal_score"))
    trend = ta.get("trend", {}).get("overall", "")

    if signal in ("STRONG_SELL",):
        result["flags"].append({"category": "technical", "severity": "high",
                                 "description": f"技术信号: {signal}，评分 {score}", "source": "data"})
    elif signal in ("SELL",):
        result["flags"].append({"category": "technical", "severity": "medium",
                                 "description": f"技术信号: {signal}，评分 {score}", "source": "data"})

    if trend == "bearish" and score is not None and score < 20:
        result["flags"].append({"category": "technical", "severity": "high",
                                 "description": "技术面全面走弱（趋势空头+评分<20）", "source": "data"})

    macd = ta.get("macd", {})
    if macd.get("signal") == "death_cross":
        result["flags"].append({"category": "technical", "severity": "medium",
                                 "description": "MACD死叉", "source": "data"})

    result["buy_signal"] = signal
    result["signal_score"] = score
    result["trend"] = trend
    return result


# --------------- Check 3: Lock-up Expiry ---------------

def check_lockup(symbol: str) -> dict:
    result = {"upcoming_30d": [], "flags": []}
    try:
        import akshare as ak
        df = ak.stock_restricted_release_queue_em(symbol=symbol)
        if df is None or df.empty:
            return result

        now = datetime.now()
        cutoff = now + timedelta(days=30)

        date_col = None
        for c in df.columns:
            if "日期" in str(c) or "解禁" in str(c):
                date_col = c
                break
        if date_col is None and len(df.columns) > 0:
            date_col = df.columns[0]

        if date_col:
            import pandas as pd
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            upcoming = df[(df[date_col] >= now) & (df[date_col] <= cutoff)]
            if not upcoming.empty:
                result["upcoming_30d"] = [str(d.date()) for d in upcoming[date_col].dropna()]
                result["flags"].append({
                    "category": "lockup", "severity": "medium",
                    "description": f"未来30天有{len(upcoming)}批限售股解禁",
                    "source": "data"
                })
    except Exception:
        pass
    return result


# --------------- Check 4-7: News-based risks ---------------

def check_news_risks(symbol: str, name: str = "") -> dict:
    search_name = name if name else symbol
    categories = {
        "insider": f"{search_name} 减持 股东减持",
        "earnings": f"{search_name} 业绩预告 亏损 下降",
        "regulatory": f"{search_name} 处罚 违规 监管 立案",
        "industry": f"{search_name} 行业政策 监管 限制",
    }
    result = {"flags": []}
    for cat, query in categories.items():
        data = _run_tool("search_intel.py", f'search "{query}" --max 3')
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and "results" in data:
            items = data["results"]

        result[cat] = items
        if items:
            result["flags"].append({
                "category": cat, "severity": "medium",
                "description": f"发现{len(items)}条相关{_cat_cn(cat)}信息",
                "source": "news"
            })
    return result


def _cat_cn(cat: str) -> str:
    return {"insider": "内部人减持", "earnings": "业绩预警",
            "regulatory": "监管处罚", "industry": "行业政策"}.get(cat, cat)


# --------------- Scoring ---------------

def compute_risk(flags: list) -> dict:
    score = 0
    for f in flags:
        sev = f.get("severity", "low")
        if sev == "high":
            score += 30
        elif sev == "medium":
            score += 15
        else:
            score += 5
    score = min(score, 100)

    if score >= 60:
        risk_level = "high"
    elif score >= 30:
        risk_level = "medium"
    else:
        risk_level = "low"

    veto_buy = any(f.get("severity") == "high" for f in flags)
    return {"risk_score": score, "risk_level": risk_level, "veto_buy": veto_buy}


# --------------- Main entry ---------------

def screen_risk(symbol: str, name: str = "") -> dict:
    all_flags = []
    checks = {}

    val = check_valuation(symbol)
    checks["valuation"] = {"pe": val["pe"], "pb": val["pb"], "status": val["status"]}
    all_flags.extend(val["flags"])

    tech = check_technical(symbol)
    checks["technical"] = {
        "buy_signal": tech.get("buy_signal"),
        "signal_score": tech.get("signal_score"),
        "trend": tech.get("trend"),
    }
    all_flags.extend(tech["flags"])

    lockup = check_lockup(symbol)
    checks["lockup"] = {"upcoming_30d": lockup["upcoming_30d"]}
    all_flags.extend(lockup["flags"])

    news = check_news_risks(symbol, name)
    checks["news_risks"] = {
        k: v for k, v in news.items() if k != "flags"
    }
    all_flags.extend(news["flags"])

    risk = compute_risk(all_flags)
    return {
        "symbol": symbol,
        "name": name,
        "risk_level": risk["risk_level"],
        "risk_score": risk["risk_score"],
        "veto_buy": risk["veto_buy"],
        "flags": all_flags,
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(description="Risk screening tool")
    sub = parser.add_subparsers(dest="command")

    p_screen = sub.add_parser("screen")
    p_screen.add_argument("symbol")
    p_screen.add_argument("--name", default="")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "screen":
        result = screen_risk(args.symbol, args.name)
        print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
