#!/usr/bin/env python3
"""Data source diagnostics — report which providers are usable per market.

Checks: (1) python package importable, (2) required env vars present.
Does NOT hit the network — this is a cheap capability probe, not a health check.
"""

import argparse
import importlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capabilities import ALL_MARKETS  # noqa: E402

# provider -> (import_name, required_env_vars, markets, note)
PROVIDERS = {
    "akshare": ("akshare", [], ["A"], None),
    "tushare": ("tushare", ["TUSHARE_TOKEN"], ["A"], None),
    "efinance": ("efinance", [], ["A"], None),
    "pytdx": ("pytdx", [], ["A"], "connects to public Tencent servers"),
    "yfinance": ("yfinance", [], ["HK", "US", "JP", "KR", "TW"], "quote may be delayed 15-20min"),
    "finnhub": ("finnhub", ["FINNHUB_API_KEY"], ["HK", "US"], None),
    "longbridge": (
        "longport.openapi",
        ["LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN"],
        ["HK", "US"],
        None,
    ),
    "alphavantage": ("requests", ["ALPHAVANTAGE_API_KEY"], ["US"], None),
}


def _check_provider(name: str) -> dict:
    import_name, env_vars, markets, note = PROVIDERS[name]

    # find_spec avoids executing the package's top-level code — cheaper and
    # side-effect-free vs import_module. This is a capability probe, not a call.
    try:
        spec = importlib.util.find_spec(import_name)
        pkg_ok = spec is not None
        pkg_err = None if pkg_ok else f"package '{import_name}' not installed"
    except Exception as e:
        pkg_ok = False
        pkg_err = f"package '{import_name}' not installed: {e}"

    missing_env = [v for v in env_vars if not os.environ.get(v)]

    available = pkg_ok and not missing_env
    reasons = []
    if not pkg_ok:
        reasons.append(pkg_err)
    if missing_env:
        reasons.append(f"missing env: {', '.join(missing_env)}")

    entry = {
        "name": name,
        "available": available,
        "markets": markets,
        "reason": "; ".join(reasons) if reasons else None,
    }
    if note:
        entry["note"] = note
    return entry


def diagnose(market: str = "all") -> dict:
    market = (market or "all").upper()
    all_providers = [_check_provider(p) for p in PROVIDERS]

    markets_to_report = ALL_MARKETS if market == "ALL" else [market]

    result = {
        "meta": {
            "provider": "diagnostics",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "instant",
            "fallback_used": False,
            "warnings": [],
        },
        "markets": [],
    }

    for m in markets_to_report:
        providers = [p for p in all_providers if m in p["markets"]]
        available = [p for p in providers if p["available"]]

        warnings = []
        if not available:
            warnings.append(f"no working data source for {m}")
        # market-specific caveats
        if m == "A" and not any(p["name"] == "tushare" and p["available"] for p in providers):
            warnings.append("TUSHARE_TOKEN not set — akshare-only, some deep data unavailable")
        if (
            m in ("HK", "US")
            and not any(p["name"] == "longbridge" and p["available"] for p in providers)
            and not any(p["name"] == "finnhub" and p["available"] for p in providers)
        ):
            warnings.append(f"{m} quotes will be delayed (yfinance only)")
        if m in ("JP", "KR", "TW"):
            warnings.append(f"{m} is served by yfinance only — quotes delayed 15-20min, no failover")

        result["markets"].append(
            {
                "market": m,
                "available": bool(available),
                "providers": providers,
                "warnings": warnings,
            }
        )

    return result


def main():
    parser = argparse.ArgumentParser(description="Data source diagnostics")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("check")
    p.add_argument(
        "--market",
        default="ALL",
        type=str.upper,
        choices=ALL_MARKETS + ["ALL"],
        help="Market to diagnose (default: ALL)",
    )
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = diagnose(args.market)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
