#!/usr/bin/env python3
"""AlphaSift L1 — multi-factor stock screener with hard filter + scoring.

L1: snapshot → hard filters → value/momentum/liquidity composite.
Regime gate: market sentiment temperature scales the final score (multiplier in [0.8, 1.2]).
L2 (opt-in via --l2 / config l2: true): per-candidate quality/growth/momentum/volatility/moneyflow factors.
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_review import calc_temperature
from stock_data import compute_market_stats

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

L2_WEIGHTS = {
    "value": 0.15,
    "quality": 0.20,
    "growth": 0.15,
    "momentum": 0.20,
    "liquidity": 0.10,
    "moneyflow": 0.15,
    "low_volatility": 0.05,
}

_INDEX_REGION = {"A": "cn", "HK": "hk", "US": "us"}


def _fetch_json(args: list[str], timeout: int = 30) -> dict | list | None:
    """Run a stock_data.py CLI command and parse its JSON stdout; None on any failure."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_data.py")
    try:
        r = subprocess.run(
            [sys.executable, script, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def fetch_snapshot(market: str) -> list:
    # a cold full-market snapshot can exceed 30s (CI runners) — allow longer
    return _fetch_json(["market_snapshot", "--market", market], timeout=120)


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


def sentiment_multiplier(score: float) -> float:
    return round(0.8 + score / 100 * 0.4, 3)  # linear map to [0.8, 1.2]


def compute_sentiment(market: str, snapshot: list | None = None) -> dict:
    """Market sentiment gate; degrades to a neutral multiplier of 1.0 on any failure."""
    fallback = {"score": None, "level": "unknown", "signal": None, "multiplier": 1.0}
    region = _INDEX_REGION.get(market)
    if region is None:
        return fallback
    try:
        stats = None
        if isinstance(snapshot, list) and snapshot:
            try:
                stats = compute_market_stats(snapshot)
            except Exception:
                stats = None
        indices = _fetch_json(["market_indices", "--region", region])
        if not isinstance(indices, list) or not indices:
            return fallback
        temp = calc_temperature(stats, indices)
        score = temp.get("score")
        if not isinstance(score, (int, float)):
            return fallback
        return {
            "score": score,
            "level": temp.get("level", "unknown"),
            "signal": temp.get("signal"),
            "multiplier": sentiment_multiplier(float(score)),
        }
    except Exception:
        return fallback


def _enrich_one(symbol: str, market: str) -> dict:
    """Fetch raw L2 indicators for one candidate; missing factor sources just yield missing keys."""
    result: dict = {}
    try:
        kline = _fetch_json(["kline", symbol, "--period", "daily", "--count", "250"])
        if isinstance(kline, list) and len(kline) >= 2:
            closes = pd.Series([float(k["close"]) for k in kline if k.get("close") is not None])
            highs = [float(k["high"]) for k in kline if k.get("high") is not None]
            if len(closes) >= 21:
                result["ret_20d"] = round(float(closes.iloc[-1] / closes.iloc[-21] - 1) * 100, 2)
            if len(closes) >= 61:
                result["ret_60d"] = round(float(closes.iloc[-1] / closes.iloc[-61] - 1) * 100, 2)
            if len(closes) and highs:
                peak = max(highs)
                if peak > 0:
                    result["high_250_proximity"] = round(float(closes.iloc[-1]) / peak, 4)
            rets = closes.pct_change().dropna().tail(20)
            if len(rets) >= 2:
                result["vol20"] = round(float(rets.std()) * float(np.sqrt(252)), 4)
    except Exception:
        pass

    if market == "A":
        fund = _fetch_json(["fundamental_context", symbol])
        if isinstance(fund, dict) and "error" not in fund:
            prof = fund.get("profitability") or {}
            for key in ("roe", "gross_margin", "net_margin"):
                if prof.get(key) is not None:
                    result[key] = prof[key]
            growth = fund.get("growth") or {}
            for key in ("revenue_yoy", "net_income_yoy"):
                if growth.get(key) is not None:
                    result[key] = growth[key]
    else:
        fin = _fetch_json(["financials", symbol])
        if isinstance(fin, dict) and "error" not in fin:
            for key in ("roe", "profit_margin"):
                if fin.get(key) is not None:
                    result[key] = fin[key]

    if market == "A":
        flow = _fetch_json(["capital_flow", symbol, "--mode", "summary"])
        if isinstance(flow, dict) and "error" not in flow and flow.get("main_net_5d") is not None:
            result["main_net_5d"] = flow["main_net_5d"]
    return result


def _cross_sectional_scores(enrichments: dict) -> dict:
    """Min-max normalize enrichment factors across the candidate set; groups fully missing are skipped."""
    fdf = pd.DataFrame.from_dict(enrichments, orient="index") if enrichments else pd.DataFrame()
    out: dict = {sym: {} for sym in fdf.index}

    def col(name: str) -> pd.Series | None:
        return pd.to_numeric(fdf[name], errors="coerce") if name in fdf.columns else None

    def add_group(group: str, parts: list):
        valid = [p for p in parts if p is not None and p.notna().any()]
        if not valid:
            return
        combined = pd.concat(valid, axis=1).mean(axis=1)
        for sym, v in combined.items():
            if not pd.isna(v):
                out[sym][group] = float(v)

    margin = col("gross_margin")
    profit_margin = col("profit_margin")
    if margin is None:
        margin = profit_margin
    elif profit_margin is not None:
        margin = margin.combine_first(profit_margin)
    add_group("quality", [_minmax(s) for s in (col("roe"), margin, col("net_margin")) if s is not None])
    add_group(
        "growth", [_minmax(s.clip(-50, 100)) for s in (col("revenue_yoy"), col("net_income_yoy")) if s is not None]
    )
    add_group(
        "momentum",
        [_minmax(s) for s in (col("ret_20d"), col("ret_60d"), col("high_250_proximity")) if s is not None],
    )

    flow = col("main_net_5d")
    if flow is not None:
        add_group("moneyflow", [_minmax(flow)])

    vol = col("vol20")
    if vol is not None:
        add_group("low_volatility", [100 - _minmax(vol)])

    return out


def _apply_l2(candidates: list, market: str, multiplier: float) -> None:
    """Enrich candidates with L2 factors and rewrite each scores['final'] as composite_l2 × multiplier."""
    symbols = [c.get("symbol") for c in candidates]
    enrichments: dict = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_enrich_one, sym, market): sym for sym in symbols if sym}
        for future, sym in futures.items():
            try:
                enrichments[sym] = future.result() or {}
            except Exception:
                enrichments[sym] = {}

    factor_scores = _cross_sectional_scores(enrichments)

    for c in candidates:
        sym = c.get("symbol")
        groups = factor_scores.get(sym, {})
        c["enriched"] = bool(enrichments.get(sym))
        scores = c["scores"]
        components = {
            "value": scores["value"],
            "liquidity": scores["liquidity"],
        }
        # momentum is L2-only: candidates without kline factors drop the group and renormalize weights.
        components.update(
            {g: groups[g] for g in ("momentum", "quality", "growth", "moneyflow", "low_volatility") if g in groups}
        )
        scores.update(
            {g: round(groups[g], 1) for g in ("quality", "growth", "moneyflow", "low_volatility") if g in groups}
        )
        total_weight = sum(L2_WEIGHTS[g] for g in components)
        composite_l2 = sum(L2_WEIGHTS[g] * components[g] for g in components) / total_weight
        scores["final"] = round(composite_l2 * multiplier, 1)


def screen(market: str, top: int, config: dict | None, l2: bool = False) -> dict:
    raw = fetch_snapshot(market)
    if isinstance(raw, dict) and "error" in raw:
        return raw
    if raw is None:
        return {"error": "failed to fetch market snapshot", "market": market}

    l2_enabled = bool(l2 or (config or {}).get("l2"))
    sentiment = compute_sentiment(market, raw if isinstance(raw, list) else None)
    multiplier = sentiment["multiplier"]

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
            "market_sentiment": sentiment,
            "l2_enabled": l2_enabled,
        }

    df = compute_scores(df)

    # L2 re-scores by final afterwards; config sort_by/sort_order only apply to the non-L2 path.
    if l2_enabled or not config:
        sort_by, ascending = "composite_score", False
    else:
        sort_by = config.get("sort_by", "composite_score")
        ascending = config.get("sort_order", "desc") == "asc"
    if sort_by not in df.columns:
        sort_by, ascending = "composite_score", False
    df = df.sort_values(sort_by, ascending=ascending)

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
        composite = float(row.get("composite_score", 0))
        entry["scores"] = {
            "value": round(float(row.get("value_score", 0)), 1),
            "momentum": round(float(row.get("momentum_score", 0)), 1),
            "liquidity": round(float(row.get("liquidity_score", 0)), 1),
            "composite": round(composite, 1),
            "final": round(composite * multiplier, 1),
        }
        candidates.append(entry)

    if l2_enabled:
        _apply_l2(candidates, market, multiplier)
        candidates.sort(key=lambda c: c["scores"]["final"], reverse=True)
        for rank, entry in enumerate(candidates, 1):
            entry["rank"] = rank

    return {
        "market": market,
        "total_stocks": total,
        "filtered_count": filtered_count,
        "returned_count": len(candidates),
        "filters_applied": filters,
        "candidates": candidates,
        "market_sentiment": sentiment,
        "l2_enabled": l2_enabled,
    }


def main():
    parser = argparse.ArgumentParser(description="AlphaSift L1 stock screener")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("screen")
    p.add_argument("--market", default="A", choices=["A", "HK", "US"])
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--config", help="Path to YAML config file")
    p.add_argument(
        "--l2", action="store_true", help="Enable L2 factor enrichment (quality/growth/momentum/volatility/moneyflow)"
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = load_config(args.config) if args.config else None
    result = screen(args.market, args.top, config, l2=args.l2)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
