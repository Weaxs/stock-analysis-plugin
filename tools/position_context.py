#!/usr/bin/env python3
"""Position context analyzer — pnl, distance to stop-loss/take-profit, position advice.

Not a portfolio system. Stateless: user passes cost/quantity/stops each call.
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


def _risk_level(distance_to_stop_pct: float, pnl_pct: float, trend: str) -> str:
    # ponytail: simple 3-bucket heuristic, upgrade to volatility-adjusted if needed
    if distance_to_stop_pct is not None and distance_to_stop_pct < 3:
        return "high"
    if pnl_pct < -5 or trend == "bearish":
        return "high"
    if pnl_pct < 0 or (distance_to_stop_pct is not None and distance_to_stop_pct < 6):
        return "medium"
    return "low"


def _advice(pnl_pct: float, distance_to_stop_pct, distance_to_take_pct, trend: str, stop_loss, cost) -> str:
    if distance_to_stop_pct is not None and distance_to_stop_pct < 3:
        return "接近止损，建议减仓或严守止损位"
    if pnl_pct > 15 and distance_to_take_pct is not None and distance_to_take_pct < 5:
        return "接近目标价，考虑分批止盈"
    if pnl_pct > 8 and stop_loss is not None and stop_loss < cost:
        return "已有盈利，建议将止损上移到成本价或以上"
    if pnl_pct < -5 and trend == "bearish":
        return "浮亏且趋势偏空，控制仓位避免加仓"
    if trend == "bullish" and pnl_pct >= 0:
        return "趋势配合，可持有观察后续量能"
    return "维持现有策略，关注关键位变化"


def analyze_position(
    symbol: str,
    cost: float,
    quantity: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> dict:
    quote = _run_json("stock_data.py", ["quote", symbol]) or {}
    tech = _run_json("technical.py", ["analyze", symbol, "--period", "daily", "--count", "60"]) or {}

    price = quote.get("price")
    if price is None:
        return {"symbol": symbol, "error": "cannot fetch current price"}
    price = float(price)

    pnl_per_share = price - cost
    pnl_pct = (pnl_per_share / cost * 100) if cost else 0.0
    market_value = price * quantity
    total_pnl = pnl_per_share * quantity

    distance_to_stop_pct = None
    if stop_loss is not None:
        distance_to_stop_pct = (price - stop_loss) / price * 100 if price else None

    distance_to_take_pct = None
    if take_profit is not None:
        distance_to_take_pct = (take_profit - price) / price * 100 if price else None

    trend = (tech.get("trend") or {}).get("overall", "unknown")
    support = (tech.get("support_resistance") or {}).get("support")
    resistance = (tech.get("support_resistance") or {}).get("resistance")

    risk_level = _risk_level(distance_to_stop_pct, pnl_pct, trend)
    advice = _advice(pnl_pct, distance_to_stop_pct, distance_to_take_pct, trend, stop_loss, cost)

    return {
        "meta": {
            "provider": "position_context",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "realtime_or_latest_trade_day",
            "fallback_used": False,
            "warnings": [],
        },
        "symbol": symbol,
        "market": detect_market(symbol),
        "name": quote.get("name"),
        "position": {
            "cost": cost,
            "quantity": quantity,
            "current_price": price,
            "market_value": round(market_value, 2),
            "total_pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        },
        "levels": {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "support": support,
            "resistance": resistance,
            "distance_to_stop_loss_pct": round(distance_to_stop_pct, 2) if distance_to_stop_pct is not None else None,
            "distance_to_take_profit_pct": round(distance_to_take_pct, 2) if distance_to_take_pct is not None else None,
        },
        "trend": trend,
        "risk_level": risk_level,
        "position_advice": advice,
    }


def main():
    parser = argparse.ArgumentParser(description="Position context analyzer")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("analyze")
    p.add_argument("symbol")
    p.add_argument("--cost", type=float, required=True, help="Cost basis per share")
    p.add_argument("--quantity", type=float, required=True, help="Number of shares held")
    p.add_argument("--stop-loss", type=float, help="Stop loss price")
    p.add_argument("--take-profit", type=float, help="Take profit price")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = analyze_position(
        args.symbol,
        cost=args.cost,
        quantity=args.quantity,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
    )
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
