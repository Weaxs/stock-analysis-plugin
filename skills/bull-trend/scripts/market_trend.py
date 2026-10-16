#!/usr/bin/env python3
"""Stateless monthly trend experiment from stock and market OHLCV JSON arrays."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from _subproc import json_safe, utf8_stdio  # noqa: E402
from backtest import compute_metrics, simulate_monthly_market  # noqa: E402
from technical import calc_trend_horizons, to_dataframe  # noqa: E402


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def read_prices(path):
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records or any(not isinstance(row, dict) for row in records):
        raise ValueError("Expected a nonempty OHLCV JSON array")
    frame = to_dataframe(records)
    fields = ["open", "high", "low", "close", "volume"]
    if "date" not in frame or any(field not in frame for field in fields):
        raise ValueError("OHLCV input requires date, open, high, low, close and volume")
    if not np.isfinite(frame[fields]).all().all() or (frame[fields[:4]] <= 0).any().any():
        raise ValueError("OHLCV input requires finite positive prices")
    if (
        (frame.low > frame.close) | (frame.close > frame.high) | (frame.low > frame.open) | (frame.open > frame.high)
    ).any():
        raise ValueError("OHLCV input requires low <= open/close <= high")
    return frame


def main():
    parser = Parser(description=__doc__)
    parser.add_argument("prices")
    parser.add_argument("market_prices")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--market-symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end")
    parser.add_argument("--capital", type=float, default=1000000)
    parser.add_argument("--allocation", type=float, default=1)
    try:
        args = parser.parse_args()
        prices, market = read_prices(args.prices), read_prices(args.market_prices)
        if args.end:
            prices = prices[prices.date <= args.end].copy()
        result = simulate_monthly_market(prices, market, args.capital, args.symbol, args.start, args.allocation)
        result.update(symbol=args.symbol, market_symbol=args.market_symbol, trend_horizons=calc_trend_horizons(prices))
        start = result["start"]
        result["equity_curve"] = [row for row in result["equity_curve"] if row["date"] >= start]
        end = result["equity_curve"][-1]["date"]
        result["metrics"] = compute_metrics(
            result["trades"], result["equity_curve"], args.capital, result["final_equity"], start, end
        )
        result["warnings"] = [
            "Research only: market close>SMA200 gates new monthly entries, not existing exits. "
            "Need stock 253 and market 200 valid closes before observation; verify all bars are closed. "
            "Three initially equal sleeves drift; costs are commission/slippage 0.1% each side, tax/interest zero. "
            "Historical splits/dividends, actual board lots, limits and liquidity need separate verification. "
            "Passing one small historical cohort is not stable cross-market validation."
        ]
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(json_safe(result), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    utf8_stdio()
    sys.exit(main())
