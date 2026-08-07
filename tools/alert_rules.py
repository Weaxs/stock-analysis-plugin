#!/usr/bin/env python3
"""Stateless alert rule check — evaluate user rules against current market snapshot.

Does NOT store history, does NOT push, does NOT schedule. Host agent decides when to poll.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_data import detect_market  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent


def _find_python() -> str:
    venv = TOOLS_DIR.parent / ".venv" / "bin" / "python3"
    return str(venv) if venv.exists() else sys.executable


def _run_json(script: str, args: list[str], timeout: int = 30):
    cmd = [_find_python(), str(TOOLS_DIR / script)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


RULE_TYPES = {
    "price_below",
    "price_above",
    "change_pct_above",
    "change_pct_below",
    "volume_ratio_above",
    "anomaly",
    "risk_veto",
    "risk_level_at_least",
}


def _severity_for(rule_type: str) -> str:
    return {
        "risk_veto": "high",
        "risk_level_at_least": "high",
        "anomaly": "medium",
        "price_below": "medium",
        "price_above": "medium",
        "change_pct_above": "medium",
        "change_pct_below": "medium",
        "volume_ratio_above": "medium",
    }.get(rule_type, "low")


def _check_rule(rule: dict, quote: dict, tech: dict, anom: dict, risk: dict) -> dict | None:
    rtype = rule.get("type")
    value = rule.get("value")
    if rtype not in RULE_TYPES:
        return {"rule": rtype, "triggered": False, "error": f"unknown rule type: {rtype}"}

    price = quote.get("price")
    change_pct = quote.get("change_pct")

    if rtype == "price_below":
        if price is not None and value is not None and float(price) < float(value):
            return {
                "rule": rtype,
                "triggered": True,
                "value": value,
                "actual": price,
                "severity": _severity_for(rtype),
                "message": f"现价 {price} 跌破 {value}",
            }
    elif rtype == "price_above":
        if price is not None and value is not None and float(price) > float(value):
            return {
                "rule": rtype,
                "triggered": True,
                "value": value,
                "actual": price,
                "severity": _severity_for(rtype),
                "message": f"现价 {price} 突破 {value}",
            }
    elif rtype == "change_pct_above":
        if change_pct is not None and value is not None and float(change_pct) > float(value):
            return {
                "rule": rtype,
                "triggered": True,
                "value": value,
                "actual": change_pct,
                "severity": _severity_for(rtype),
                "message": f"涨幅 {change_pct}% 超过 {value}%",
            }
    elif rtype == "change_pct_below":
        if change_pct is not None and value is not None and float(change_pct) < float(value):
            return {
                "rule": rtype,
                "triggered": True,
                "value": value,
                "actual": change_pct,
                "severity": _severity_for(rtype),
                "message": f"跌幅 {change_pct}% 低于 {value}%",
            }
    elif rtype == "volume_ratio_above":
        vol_data = (tech or {}).get("volume", {})
        ratio = vol_data.get("volume_ratio")
        if ratio is not None and value is not None and float(ratio) > float(value):
            return {
                "rule": rtype,
                "triggered": True,
                "value": value,
                "actual": ratio,
                "severity": _severity_for(rtype),
                "message": f"量比 {ratio} 超过 {value}",
            }
    elif rtype == "anomaly":
        # value is an anomaly type string, e.g. "macd_golden_cross"
        anomalies = (anom or {}).get("anomalies", [])
        for a in anomalies:
            if a.get("type") == value:
                return {
                    "rule": rtype,
                    "triggered": True,
                    "value": value,
                    "severity": a.get("severity", "medium"),
                    "message": a.get("description", f"命中异动 {value}"),
                }
    elif rtype == "risk_veto":
        if (risk or {}).get("veto_buy"):
            return {"rule": rtype, "triggered": True, "severity": "high", "message": "风险筛查触发一票否决"}
    elif rtype == "risk_level_at_least":
        order = {"low": 0, "medium": 1, "high": 2}
        cur = order.get((risk or {}).get("risk_level"))
        want = order.get(value)
        if cur is not None and want is not None and cur >= want:
            return {
                "rule": rtype,
                "triggered": True,
                "value": value,
                "actual": (risk or {}).get("risk_level"),
                "severity": "high",
                "message": f"风险等级 {(risk or {}).get('risk_level')} ≥ {value}",
            }

    return None


def check_rules(symbol: str, rules: list[dict]) -> dict:
    if not rules:
        return {"symbol": symbol, "error": "no rules provided"}

    needs_tech = any(r.get("type") == "volume_ratio_above" for r in rules)
    needs_anom = any(r.get("type") == "anomaly" for r in rules)
    needs_risk = any(r.get("type") in ("risk_veto", "risk_level_at_least") for r in rules)

    quote = _run_json("stock_data.py", ["quote", symbol]) or {}
    tech = _run_json("technical.py", ["analyze", symbol, "--period", "daily", "--count", "60"]) if needs_tech else None
    anom = _run_json("anomaly_detect.py", ["detect", symbol]) if needs_anom else None
    risk = _run_json("risk_screening.py", ["screen", symbol]) if needs_risk else None

    warnings = []
    if not quote or quote.get("price") is None:
        warnings.append("quote fetch failed or missing price — price/change rules cannot fire")
    if needs_tech and not tech:
        warnings.append("technical analysis fetch failed — volume_ratio rules cannot fire")
    if needs_anom and not anom:
        warnings.append("anomaly fetch failed — anomaly rules cannot fire")
    if needs_risk and not risk:
        warnings.append("risk screening fetch failed — risk rules cannot fire")

    hits = []
    evaluated = []
    for rule in rules:
        result = _check_rule(rule, quote, tech, anom, risk)
        if result is None:
            evaluated.append({"rule": rule.get("type"), "triggered": False})
        else:
            evaluated.append(result)
            if result.get("triggered"):
                hits.append(result)

    return {
        "meta": {
            "provider": "alert_rules",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "realtime_or_latest_trade_day",
            "fallback_used": False,
            "warnings": warnings,
        },
        "symbol": symbol,
        "market": detect_market(symbol),
        "triggered": len(hits) > 0,
        "hit_count": len(hits),
        "hits": hits,
        "evaluated": evaluated,
    }


def main():
    parser = argparse.ArgumentParser(description="Stateless alert rule check")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("check")
    p.add_argument("symbol")
    p.add_argument("--rules", help='JSON array of rules, or "-" for stdin')
    p.add_argument("--rules-b64", help="Base64-encoded JSON rules")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.rules_b64:
        import base64

        raw = base64.b64decode(args.rules_b64).decode("utf-8")
    elif args.rules == "-" or args.rules is None:
        raw = sys.stdin.read()
    else:
        raw = args.rules
    try:
        rules = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid rules JSON: {e}"}, ensure_ascii=False))
        sys.exit(1)

    result = check_rules(args.symbol, rules)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
