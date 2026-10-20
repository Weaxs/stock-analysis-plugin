#!/usr/bin/env python3
"""Deterministic rotation metrics and allocation ablation; no unspecified neural model."""

import argparse
import json
import math
import re
import stat
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _subproc import run_tool, utf8_stdio

CALENDARS = {"A": "XSHG", "HK": "XHKG", "US": "XNYS", "JP": "XTKS", "KR": "XKRX", "TW": "XTAI"}
PRICE_BASES = {"qfq", "total_return", "split_adjusted"}


class DataQualityError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def confidence(samples):
    if not isinstance(samples, list) or not 2 <= len(samples) <= 100000:
        raise ValueError("Require 2..100000 externally generated MC-dropout predictions")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in samples):
        raise ValueError("Predictions must be finite JSON numbers")
    ordered = sorted(samples)
    median = statistics.mean(ordered[(len(ordered) - 1) // 2 : len(ordered) // 2 + 1])
    sigma = statistics.pstdev(samples)
    mass = sum(abs(value - median) <= sigma for value in samples) / len(samples)
    return {
        "sample_count": len(samples),
        "median": median,
        "population_std": sigma,
        "mass_within_one_std_of_median": mass,
        "concentration_criterion_met": mass >= 0.8 if sigma > 0 else None,
        "unavailable_reason": "zero_dispersion_cannot_verify_dropout" if sigma == 0 else None,
        "is_buy_signal": False,
        "method": "Empirical mass within median +/- population SD; inclusive boundaries, threshold >= 0.80",
        "limitations": "Concentration is not profitability or calibration; MC sampling settings are caller supplied",
    }


def allocation(data):
    if not isinstance(data, list) or not 1 <= len(data) <= 100:
        raise ValueError("Require 1..100 symbol trade-count records")
    rows, symbols = [], set()
    for row in data:
        if not isinstance(row, dict) or set(row) != {"symbol", "buys", "wins", "streak"}:
            raise ValueError("Each record requires only symbol, buys, wins and streak")
        symbol = row["symbol"]
        if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip() or symbol in symbols:
            raise ValueError("Symbol labels must be nonempty, distinct and trimmed")
        symbols.add(symbol)
        buys, wins, streak = (row[key] for key in ("buys", "wins", "streak"))
        if any(type(value) is not int or not 0 <= value <= 1000000000 for value in (buys, wins, streak)):
            raise ValueError("Counts must be nonnegative integers up to one billion")
        if not streak <= wins <= buys:
            raise ValueError("Require streak <= wins <= buys")
        rows.append(
            {
                **row,
                "raw_weight": 1.0 + wins / buys + streak / (wins + 1) if buys else None,
                "unavailable_reason": "no_paper_initialization_for_zero_buys" if not buys else None,
            }
        )
    return {
        "assets": rows,
        "is_allocation_plan": False,
        "method": "Section 2.3: w = 1 + wins/buys + streak/(wins+1)",
        "limitations": "Counts must come from closed trades before the decision; no model selection or cash allocation",
    }


def analyze(payload):
    if not isinstance(payload, dict) or not payload or set(payload) - {"prediction_samples", "trade_counts"}:
        raise ValueError("Provide prediction_samples and/or trade_counts")
    result = {
        "model_status": "not_implemented_missing_specification",
        "paper_reproduction": "partial_calculations_only",
        "is_buy_signal": False,
    }
    if "prediction_samples" in payload:
        rows = payload["prediction_samples"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError("Require 1..100 prediction sample groups")
        groups, total = {}, 0
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"symbol", "samples"}:
                raise ValueError("Each sample group requires symbol and samples")
            symbol = row["symbol"]
            if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip() or symbol in groups:
                raise ValueError("Require distinct nonempty symbol labels")
            groups[symbol] = confidence(row["samples"])
            total += groups[symbol]["sample_count"]
            if total > 100000:
                raise ValueError("At most 100000 predictions per request")
        result["confidence"] = groups
    if "trade_counts" in payload:
        result["allocation"] = allocation(payload["trade_counts"])
    return result


def iso_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Require YYYY-MM-DD")
    return value


def number(value, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("Number outside documented bounds")
    return value


def symbol_code(value):
    from stock_data import detect_market
    from trend_strategy import request_symbol

    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,19}", value):
        raise ValueError("Require a stock/ETF code")
    value = request_symbol(value)
    patterns = {
        "A": r"\d{6}",
        "HK": r"\d{4,5}\.HK",
        "US": r"[A-Z][A-Z0-9-]{0,14}",
        "JP": r"[A-Z0-9]{4,5}\.T",
        "KR": r"\d{6}\.(KS|KQ)",
        "TW": r"\d{4,6}\.(TW|TWO)",
    }
    if not re.fullmatch(patterns[detect_market(value)], value):
        raise ValueError("Use bare six-digit A codes or canonical exchange suffixes")
    return value


def fetch_dataset(symbol, count):
    from stock_data import detect_market

    bars = run_tool("stock_data.py", ["kline", symbol, "--period", "daily", "--count", str(count)], timeout=30)
    if not isinstance(bars, list) or not bars or len(bars) > count:
        raise ValueError("History unavailable")
    if detect_market(symbol) == "A":
        currency = "CNY"
    else:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        ticker.history(period="5d", timeout=10)
        currency = ticker.get_history_metadata().get("currency")
    bases = {row.get("adjusted") for row in bars if isinstance(row, dict)}
    if len(bases) != 1:
        raise DataQualityError("Mixed adjustment basis")
    return {"symbol": symbol, "currency": currency, "price_basis": bases.pop(), "bars": bars}


def backtest(payload):
    import exchange_calendars as xcals
    import pandas as pd
    from stock_data import detect_market

    fields = {
        "datasets",
        "symbols",
        "start_date",
        "end_date",
        "history_start_date",
        "warmup_weeks",
        "capital",
        "cost_per_side",
    }
    if not isinstance(payload, dict) or set(payload) - fields or ("symbols" in payload) == ("datasets" in payload):
        raise ValueError("Provide symbols or datasets, not both")
    start, end = (iso_date(payload[k]) for k in ("start_date", "end_date"))
    if start >= end or end > str(pd.Timestamp.now(tz="UTC").date()):
        raise ValueError("Require a past ordered evaluation interval")
    capital = number(payload.get("capital", 1000000), 0.01, 1e12)
    cost = number(payload.get("cost_per_side", 0.001), 0, 0.1)
    warmup = number(payload.get("warmup_weeks", 26), 1, 104)
    if int(warmup) != warmup:
        raise ValueError("warmup_weeks must be an integer")
    if "symbols" in payload:
        symbols = payload["symbols"]
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= 20:
            raise ValueError("Require 1..20 symbols")
        symbols = [symbol_code(s) for s in symbols]
        if len(set(symbols)) != len(symbols):
            raise ValueError("Duplicate symbol aliases")
        history_start = iso_date(
            payload.get("history_start_date", str((pd.Timestamp(start) - pd.DateOffset(years=1)).date()))
        )
        if history_start >= start:
            raise ValueError("history_start_date must precede evaluation")
        count = (pd.Timestamp.now(tz="UTC").date() - date.fromisoformat(history_start)).days + 60
        if not 1 <= count <= 20000 or count * len(symbols) > 100000:
            raise ValueError("Requested history exceeds bounded acquisition")

        def fetch(symbol):
            try:
                return fetch_dataset(symbol, count)
            except Exception:
                return {"symbol": symbol, "error": "History/currency metadata unavailable"}

        with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as pool:
            datasets = list(pool.map(fetch, symbols))
        failed = [d["symbol"] for d in datasets if "error" in d]
        if failed:
            return {
                "error": "Cannot load the complete pool; no backtest produced",
                "error_code": "incomplete_pool",
                "failed_symbols": failed,
            }
        datasets = [{**d, "bars": [b for b in d["bars"] if b["date"] >= history_start]} for d in datasets]
    else:
        if "history_start_date" in payload:
            raise ValueError("Explicit datasets supply their own warmup history")
        datasets = payload["datasets"]
    if not isinstance(datasets, list) or not 1 <= len(datasets) <= 20:
        raise ValueError("Require 1..20 datasets")
    groups, seen, total = {}, set(), 0
    now = pd.Timestamp.now(tz="UTC")
    for dataset in datasets:
        if not isinstance(dataset, dict) or set(dataset) != {"symbol", "currency", "price_basis", "bars"}:
            raise ValueError("Dataset requires symbol, currency, price_basis and bars")
        symbol = symbol_code(dataset["symbol"])
        if symbol in seen:
            raise ValueError("Duplicate symbol aliases")
        seen.add(symbol)
        market, currency = detect_market(symbol), dataset["currency"]
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("Require confirmed quote currency, not an inferred country currency")
        if dataset["price_basis"] not in PRICE_BASES:
            raise DataQualityError("Unverified adjusted prices")
        bars = dataset["bars"]
        if not isinstance(bars, list) or not 2 <= len(bars) <= 20000:
            raise ValueError("Require 2..20000 dated OHLCV bars")
        total += len(bars)
        if total > 100000:
            raise ValueError("At most 100000 price rows per request")
        dates = [iso_date(row["date"]) for row in bars]
        if dates != sorted(set(dates)):
            raise DataQualityError("Unordered or duplicate dates")
        frame = pd.DataFrame(bars).loc[[d <= end for d in dates]].copy()
        if frame.empty:
            raise DataQualityError("No prices in interval")
        for row in frame.to_dict("records"):
            values = {key: number(row[key], 0, 1e16) for key in ("open", "high", "low", "close", "volume")}
            lower, upper = min(values["open"], values["close"]), max(values["open"], values["close"])
            if (
                values["low"] <= 0
                or (values["low"] > lower and not math.isclose(values["low"], lower, rel_tol=1e-12))
                or (values["high"] < upper and not math.isclose(values["high"], upper, rel_tol=1e-12))
            ):
                raise DataQualityError("Invalid OHLC range")
            if "adjusted" in row and row["adjusted"] != dataset["price_basis"]:
                raise DataQualityError("Mixed price basis")
        frame.index = pd.to_datetime(frame.date)
        ratios = frame.close / frame.close.shift(1)
        # shortcut: legitimate extreme moves are rejected; add source-backed exceptions when a verified pool needs them.
        if ((ratios > 1.5) | (ratios < 1 / 1.5) | (frame.high / frame.low > 1.5)).any():
            raise DataQualityError("Price discontinuity requires independent corporate-action review")
        calendar = xcals.get_calendar(
            CALENDARS[market],
            start=str(frame.index.min().to_period("W-FRI").start_time.date()),
            end=str((pd.Timestamp(end) + pd.Timedelta(days=8)).date()),
        )
        for day in frame.index:
            if not calendar.is_session(day):
                raise DataQualityError("Prices on a non-session date")
            closed = calendar.session_close(day) + (pd.Timedelta(minutes=10) if market == "HK" else pd.Timedelta(0))
            if closed >= now:
                raise DataQualityError("Unclosed price bar")
        groups.setdefault((market, currency), {})[symbol] = (frame, dataset["price_basis"])
    results = []
    for (market, currency), members in sorted(groups.items()):
        results.append(simulate_allocation(market, currency, members, start, end, int(warmup), capital, cost))
    return {
        "paper_reproduction": "allocation_ablation_only",
        "model_status": "not_implemented_missing_specification",
        "capital_scope": "independent_per_market_and_quote_currency",
        "capital_per_group": capital,
        "requested_period": {"start": start, "end": end},
        "cost_per_side": cost,
        "groups": results,
        "warnings": [
            "Both arms buy every pool member each full exchange week; this is not the neural model or its selection, ROC, MC-dropout or loss filters.",
            "Historical raw scores are normalized to budgets after explicit observed-trade warmup; this initialization and normalization are experimental conventions, not a full paper reproduction.",
            "Fractional adjusted-price notional units and proportional entry/exit costs; settlement, FX, lots, price limits, order queues, liquidity and taxes are not simulated. No real orders or holdings are stored.",
            "Groups use independent starting capital and confirmed quote currencies; there is no combined cross-currency portfolio return.",
            "Price basis and currency evidence are caller/provider supplied; discontinuity checks flag suspicious data but do not prove correctness or absence of survivorship bias.",
        ],
    }


def simulate_allocation(market, currency, members, start, end, warmup, capital, cost):
    import exchange_calendars as xcals
    import pandas as pd
    from backtest import compute_metrics

    first = min(frame.index.min() for frame, _basis in members.values())
    first = first.to_period("W-FRI").start_time
    boundary = pd.Timestamp(end).to_period("W-FRI").end_time.normalize()
    calendar = xcals.get_calendar(
        CALENDARS[market],
        start=str((first - pd.Timedelta(days=7)).date()),
        end=str((boundary + pd.Timedelta(days=7)).date()),
    )
    sessions = calendar.sessions_in_range(first, boundary)
    weeks = {}
    for session in sessions:
        day = session.tz_localize(None) if session.tzinfo else session
        weeks.setdefault(str(day.to_period("W-FRI")), []).append(day)
    counts = {n: {"symbol": n, "buys": 0, "wins": 0, "streak": 0} for n in sorted(members)}
    names = list(counts)
    arms = {mode: {"equity": capital, "curve": [], "costs_paid": 0.0} for mode in ("equal_weight", "historical_weight")}
    allocations, skipped, observed, last_close = [], [], 0, None
    for week, days in weeks.items():
        if days[-1] > pd.Timestamp(end) or days[0] < pd.Timestamp(start) <= days[-1]:
            skipped.append({"week": week, "reason": "partial_evaluation_boundary"})
            continue
        if any(
            not set(days) <= set(frame.index) or (frame.loc[frame.index.intersection(days), "volume"] <= 0).any()
            for frame, _ in members.values()
        ):
            if days[0] >= pd.Timestamp(start):
                raise DataQualityError(
                    "Incomplete evaluation week; cannot select trades using future data availability"
                )
            skipped.append({"week": week, "reason": "incomplete_or_zero_volume_pool_week"})
            continue
        opens = {n: float(members[n][0].loc[days[0], "open"]) for n in names}
        gross = {n: float(members[n][0].loc[days[-1], "close"]) / opens[n] - 1 for n in names}
        if days[0] >= pd.Timestamp(start):
            if observed < warmup:
                raise ValueError("Insufficient complete pre-evaluation warmup weeks")
            raw = allocation(list(counts.values()))["assets"]
            total = math.fsum(row["raw_weight"] for row in raw)
            weights = {row["symbol"]: row["raw_weight"] / total for row in raw}
            allocations.append(
                {
                    "observation_date": last_close,
                    "entry_date": str(days[0].date()),
                    "exit_date": str(days[-1].date()),
                    "weights": weights,
                }
            )
            for mode, arm in arms.items():
                w = dict.fromkeys(names, 1 / len(names)) if mode == "equal_weight" else weights
                initial = arm["equity"]
                arm["costs_paid"] += initial * cost / (1 + cost)
                for i, day in enumerate(days):
                    equity = (
                        initial
                        / (1 + cost)
                        * math.fsum(w[n] * float(members[n][0].loc[day, "close"]) / opens[n] for n in names)
                    )
                    if i == len(days) - 1:
                        arm["costs_paid"] += equity * cost
                        equity *= 1 - cost
                    arm["curve"].append({"date": str(day.date()), "equity": equity})
                arm["equity"] = equity
        for name, value in gross.items():
            counts[name]["buys"] += 1
            counts[name]["wins"] += int(value > 0)
            counts[name]["streak"] = counts[name]["streak"] + 1 if value > 0 else 0
        observed += 1
        last_close = str(days[-1].date())
    if not allocations:
        raise ValueError("No complete evaluation week")
    result = {
        "market": market,
        "currency": currency,
        "symbols": names,
        "period": {"start": allocations[0]["entry_date"], "end": allocations[-1]["exit_date"]},
        "price_basis": {n: members[n][1] for n in names},
        "data_coverage": {
            n: {
                "first": str(frame.index.min().date()),
                "last": str(frame.index.max().date()),
                "source": sorted(set(frame["source"].dropna())) if "source" in frame else [],
            }
            for n, (frame, _basis) in members.items()
        },
        "trade_counts": list(counts.values()),
        "evaluated_weeks": len(allocations),
        "skipped_weeks": skipped,
        "allocations": allocations,
    }
    for mode, arm in arms.items():
        metrics = compute_metrics(
            [], arm["curve"], capital, arm["equity"], result["period"]["start"], result["period"]["end"]
        )
        result[mode] = {
            "final_equity": arm["equity"],
            "costs_paid": arm["costs_paid"],
            "closed_trades": len(allocations) * len(names),
            "metrics": {key: metrics[key] for key in ("total_return", "annual_return", "max_drawdown")},
        }
    result["delta_annual_return"] = (
        result["historical_weight"]["metrics"]["annual_return"] - result["equal_weight"]["metrics"]["annual_return"]
    )
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def main():
    utf8_stdio()
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("command", choices=["confidence", "allocation", "analyze", "backtest"])
        parser.add_argument("input_file", nargs="?", type=Path)
        parser.add_argument("--input", type=Path)
        args = parser.parse_args()
        if (args.input is None) == (args.input_file is None):
            raise ValueError("Provide exactly one JSON input file")
        path = args.input or args.input_file
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
            raise ValueError("Input must be a regular JSON file up to 8 MiB")
        with path.open("rb") as source:
            raw = source.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Input exceeds 8 MiB")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        result = {"confidence": confidence, "allocation": allocation, "analyze": analyze, "backtest": backtest}[
            args.command
        ](data)
        result.setdefault("paper_reproduction", "partial_calculations_only")
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 1 if "error" in result else 0
    except DataQualityError:
        print(
            json.dumps(
                {
                    "error": "Market data failed adjustment, chronology or session checks; review the full pool",
                    "error_code": "market_data_requires_review",
                }
            )
        )
        return 1
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        error = "Invalid arguments or input; check --help and the skill input contract"
    except Exception:
        error = "Rotation calculation failed unexpectedly"
    print(json.dumps({"error": error}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
