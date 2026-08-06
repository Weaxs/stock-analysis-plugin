#!/usr/bin/env python3
"""AlphaSift L1 — multi-factor stock screener with hard filter + scoring."""

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import yaml

DEFAULT_FILTERS = {
    "pe_min": 0,
    "pe_max": 100,
    "pb_min": 0,
    "pb_max": 20,
    "market_cap_min": 5e9,
    "turnover_rate_min": 0.5,
    "turnover_rate_max": 20,
    "change_pct_min": -5,
    "change_pct_max": 9.9,
    "volume_ratio_min": 0.5,
}


def fetch_snapshot(market: str) -> list:
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_data.py")
    r = subprocess.run(
        [sys.executable, script, "market_snapshot", "--market", market],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(r.stdout)


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def merge_filters(config: dict | None) -> dict:
    filters = dict(DEFAULT_FILTERS)
    if config and "filters" in config:
        filters.update(config["filters"])
    return filters


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    conditions = []
    if "pe" in df.columns:
        conditions.append(df["pe"] >= filters["pe_min"])
        conditions.append(df["pe"] <= filters["pe_max"])
    if "pb" in df.columns:
        conditions.append(df["pb"] >= filters["pb_min"])
        conditions.append(df["pb"] <= filters["pb_max"])
    if "market_cap" in df.columns:
        conditions.append(df["market_cap"] >= filters["market_cap_min"])
    if "turnover_rate" in df.columns:
        conditions.append(df["turnover_rate"] >= filters["turnover_rate_min"])
        conditions.append(df["turnover_rate"] <= filters["turnover_rate_max"])
    if "change_pct" in df.columns:
        conditions.append(df["change_pct"] >= filters["change_pct_min"])
        conditions.append(df["change_pct"] <= filters["change_pct_max"])
    if "volume_ratio" in df.columns:
        conditions.append(df["volume_ratio"] >= filters["volume_ratio_min"])

    if conditions:
        mask = conditions[0]
        for c in conditions[1:]:
            mask = mask & c
        return df[mask].copy()
    return df.copy()


def _minmax(s: pd.Series) -> pd.Series:
    mn, mx = s.min(), s.max()
    if mx == mn:
        return pd.Series(50.0, index=s.index)
    return ((s - mn) / (mx - mn) * 100).clip(0, 100)


def compute_scores(df: pd.DataFrame) -> pd.DataFrame:
    # Value: lower PE/PB → higher score (inverted)
    value_parts = []
    if "pe" in df.columns:
        value_parts.append(100 - _minmax(df["pe"]))
    if "pb" in df.columns:
        value_parts.append(100 - _minmax(df["pb"]))
    df["value_score"] = sum(value_parts) / max(len(value_parts), 1)

    # Momentum: higher change_pct and volume_ratio → higher score
    mom_parts = []
    if "change_pct" in df.columns:
        mom_parts.append(_minmax(df["change_pct"]))
    if "volume_ratio" in df.columns:
        mom_parts.append(_minmax(df["volume_ratio"]))
    df["momentum_score"] = sum(mom_parts) / max(len(mom_parts), 1)

    # Liquidity: higher turnover_rate and volume → higher score
    liq_parts = []
    if "turnover_rate" in df.columns:
        liq_parts.append(_minmax(df["turnover_rate"]))
    if "volume" in df.columns:
        liq_parts.append(_minmax(df["volume"].astype(float)))
    df["liquidity_score"] = sum(liq_parts) / max(len(liq_parts), 1)

    df["composite_score"] = 0.4 * df["value_score"] + 0.3 * df["momentum_score"] + 0.3 * df["liquidity_score"]
    return df


def screen(market: str, top: int, config: dict | None) -> dict:
    raw = fetch_snapshot(market)
    if isinstance(raw, dict) and "error" in raw:
        return raw

    df = pd.DataFrame(raw)
    total = len(df)

    for c in ["price", "change_pct", "volume", "turnover", "pe", "pb", "market_cap", "turnover_rate", "volume_ratio"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    critical = ["price", "pe", "pb"]
    existing = [c for c in critical if c in df.columns]
    if existing:
        df = df.dropna(subset=existing)

    filters = merge_filters(config)
    df = apply_filters(df, filters)
    filtered_count = len(df)

    if df.empty:
        return {
            "market": market,
            "total_stocks": total,
            "filtered_count": 0,
            "returned_count": 0,
            "filters_applied": filters,
            "candidates": [],
        }

    df = compute_scores(df)

    sort_by = config.get("sort_by", "composite_score") if config else "composite_score"
    sort_order = config.get("sort_order", "desc") if config else "desc"
    ascending = sort_order == "asc"

    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=ascending)
    else:
        df = df.sort_values("composite_score", ascending=False)

    df = df.head(top)

    candidates = []
    for rank, (_, row) in enumerate(df.iterrows(), 1):
        entry = {"rank": rank}
        for field in [
            "symbol",
            "name",
            "price",
            "change_pct",
            "volume",
            "turnover_rate",
            "pe",
            "pb",
            "market_cap",
            "volume_ratio",
        ]:
            v = row.get(field)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                entry[field] = round(float(v), 2) if isinstance(v, (float, np.floating)) else v
        entry["scores"] = {
            "value": round(float(row.get("value_score", 0)), 1),
            "momentum": round(float(row.get("momentum_score", 0)), 1),
            "liquidity": round(float(row.get("liquidity_score", 0)), 1),
            "composite": round(float(row.get("composite_score", 0)), 1),
        }
        candidates.append(entry)

    return {
        "market": market,
        "total_stocks": total,
        "filtered_count": filtered_count,
        "returned_count": len(candidates),
        "filters_applied": filters,
        "candidates": candidates,
    }


def main():
    parser = argparse.ArgumentParser(description="AlphaSift L1 stock screener")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("screen")
    p.add_argument("--market", default="A", choices=["A", "HK", "US"])
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--config", help="Path to YAML config file")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = load_config(args.config) if args.config else None
    result = screen(args.market, args.top, config)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
