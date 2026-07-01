#!/usr/bin/env python3
"""Market capability boundaries — tell agent what a given market supports.

Prevents agents from calling A-share-only tools on HK/US and vice versa.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_data import detect_market

# tool_name -> {markets: [supported markets], reason: str for unsupported}
TOOL_MATRIX = {
    "get_kline": {"markets": ["A", "HK", "US"], "reason": None},
    "get_quote": {"markets": ["A", "HK", "US"], "reason": None},
    "get_news": {"markets": ["A", "HK", "US"], "reason": None},
    "get_financials": {"markets": ["A", "HK", "US"], "reason": None},
    "get_technical_analysis": {"markets": ["A", "HK", "US"], "reason": None},
    "analyze_pattern": {"markets": ["A", "HK", "US"], "reason": None},
    "get_market_indices": {"markets": ["A", "HK", "US"], "reason": None},
    "calculate_ma": {"markets": ["A", "HK", "US"], "reason": None},
    "get_volume_analysis": {"markets": ["A", "HK", "US"], "reason": None},
    "detect_anomaly": {"markets": ["A", "HK", "US"], "reason": None},
    "get_stock_info": {"markets": ["A", "HK", "US"], "reason": None},
    "detect_market_regime": {"markets": ["A", "HK", "US"], "reason": None},
    "get_market_review": {"markets": ["A", "HK", "US"], "reason": None},
    # A-share only
    "get_capital_flow": {"markets": ["A"], "reason": "A-share only (akshare data)"},
    "get_chip_distribution": {"markets": ["A"], "reason": "A-share only"},
    "get_sector_rankings": {"markets": ["A"], "reason": "A-share only"},
    "get_market_stats": {"markets": ["A"], "reason": "A-share only"},
    "get_fundamental_context": {"markets": ["A"], "reason": "A-share only"},
    "resolve_stock_name": {"markets": ["A"], "reason": "A-share name/pinyin index only"},
    # US-leaning
    "get_social_sentiment": {
        "markets": ["US"],
        "reason": "sentiment providers are US-centric; requires SENTIMENT_API_KEY",
    },
    "get_trending_sentiment": {
        "markets": ["A", "HK", "US"],
        "reason": None,
    },
    # search-based, market-agnostic
    "search_stock_news": {"markets": ["A", "HK", "US"], "reason": None},
    "search_comprehensive_intel": {"markets": ["A", "HK", "US"], "reason": None},
    "extract_article": {"markets": ["A", "HK", "US"], "reason": None},
    "screen_risk": {"markets": ["A", "HK", "US"], "reason": None},
}


def get_capabilities(market: str) -> dict:
    market = market.upper()
    supported = []
    unsupported = []
    for tool, spec in TOOL_MATRIX.items():
        if market in spec["markets"]:
            supported.append(tool)
        else:
            unsupported.append({"tool": tool, "reason": spec["reason"] or "not supported"})

    return {
        "meta": {
            "provider": "capabilities",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "static",
            "fallback_used": False,
            "warnings": [],
        },
        "market": market,
        "supported": supported,
        "unsupported": unsupported,
    }


def main():
    parser = argparse.ArgumentParser(description="Market capability boundaries")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("get")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--market", type=str.upper, choices=["A", "HK", "US"], help="Market code")
    grp.add_argument("--symbol", help="Stock symbol (auto-detects market)")
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    market = args.market or detect_market(args.symbol)
    result = get_capabilities(market)
    if args.symbol:
        result["symbol"] = args.symbol
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
