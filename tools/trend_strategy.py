#!/usr/bin/env python3
"""Stateless monthly signal plans with SMA200 eligibility and volatility budgets."""

import argparse
import json
import math
import re
import stat
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _subproc import run_tool, utf8_stdio
from stock_data import detect_market
from technical import _trend_prices, ma_series

MODEL = "sma200_risk_budget"
ALLOCATION = 0.8704


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("as_of and price dates require YYYY-MM-DD")
    day = pd.Timestamp(value)
    if pd.isna(day) or str(day.date()) != value:
        raise ValueError("Invalid date")
    return day


def symbol_set(payload, key, names):
    values = payload.get(key, [])
    if not isinstance(values, list) or any(type(n) is not str for n in values):
        raise ValueError(f"{key} must be a list of unique pool symbols")
    if len(values) != len(set(values)) or not set(values) <= names:
        raise ValueError(f"{key} must contain unique pool symbols")
    return set(values)


def symbol_identity(name, market):
    return int(name[:-3]) if market == "HK" else name.replace(".", "-")


def pool_members(symbols, market):
    patterns = {"A": r"\d{6}", "HK": r"\d{4,5}\.HK", "US": r"[A-Z][A-Z0-9.-]{0,14}"}
    if market not in patterns:
        raise ValueError("Rule62 supports a single A, HK or US stock pool")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 100:
        raise ValueError("A pool requires 1..100 distinct stocks")
    if any(type(n) is not str or not re.fullmatch(patterns[market], n) or detect_market(n) != market for n in symbols):
        raise ValueError("Symbols must be stocks from the declared single market")
    names = sorted(symbols, key=lambda n: symbol_identity(n, market))
    if len({symbol_identity(n, market) for n in names}) != len(names):
        raise ValueError("Duplicate symbol aliases")
    return names


def pool_state(payload, names):
    held = symbol_set(payload, "held_symbols", set(names))
    pending = symbol_set(payload, "pending_exit_symbols", set(names))
    if not pending <= held:
        raise ValueError("pending_exit_symbols must be real held_symbols")
    slots = payload.get("slots", math.ceil(len(names) / 3))
    if type(slots) not in (int, float) or not 1 <= slots <= len(names) or int(slots) != slots or len(held) > slots:
        raise ValueError("slots requires an integer in 1..N and cannot be below actual held count")
    return held, pending, int(slots)


def analyze(payload):
    allowed = {"market", "as_of", "histories", "slots", "held_symbols", "pending_exit_symbols"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Expected strategy input with only documented fields; corporate actions are not supported")
    market = payload["market"]
    as_of = iso_date(payload["as_of"])
    histories = payload["histories"]
    if not isinstance(histories, dict):
        raise ValueError("histories requires distinct stocks")
    names = pool_members(list(histories), market)
    held, pending, slots = pool_state(payload, names)
    stocks, calendar, known_vol = {}, set(), {}
    for name in names:
        records = histories[name]
        if not isinstance(records, list) or not 1 <= len(records) <= 20000:
            raise ValueError("Each history requires 1..20000 dated price rows")
        for row in records:
            if not isinstance(row, dict) or not {"date", "close", "volume"} <= row.keys():
                raise ValueError("Each price row requires date, close and volume")
            iso_date(row["date"])
            if any(type(row[k]) not in (int, float) for k in ("close", "volume")):
                raise ValueError("close and volume must be numbers, excluding booleans")
        frame = pd.DataFrame(records)
        frame["date"] = pd.to_datetime(frame.date)
        # Validate chronology before cutting the prefix; never silently sort duplicate/bad input.
        _trend_prices(frame)
        frame = frame.loc[frame.date <= as_of]
        if frame.empty:
            raise ValueError("Every pool member needs history at or before as_of")
        close, dates = _trend_prices(frame)
        calendar.update(dates)
        average = ma_series(close, 200)
        returns = np.log(close).diff()
        sigma = returns.rolling(63).std(ddof=1)
        sigma = sigma.mask(returns.rolling(63).min().eq(returns.rolling(63).max()), 0.0)
        available = not dates.empty and dates.iloc[-1] == as_of

        def last(series, available=available):
            value = series.iloc[-1] if available and not series.empty else np.nan
            return float(value) if np.isfinite(value) else None

        score = last(np.log(close) - np.log(average))
        volatility = last(sigma)
        if volatility is not None and volatility > 0:
            known_vol[name] = volatility
        basis = str(frame.adjusted.iloc[-1]) if "adjusted" in frame and pd.notna(frame.adjusted.iloc[-1]) else "unknown"
        stocks[name] = {
            "quote_available": bool(available),
            "quote_as_of": str(dates.iloc[-1].date()) if not dates.empty else None,
            "price_basis": basis,
            "valid_bars": len(close),
            "close": last(close),
            "sma200": last(average),
            "sma200_21_bars_ago": last(average.shift(21)),
            "distance": score,
            "sigma63": volatility,
            "rising_sma200": bool(available and len(close) >= 221 and average.iloc[-1] > average.shift(21).iloc[-1]),
            "new_entry_eligible": False,
            "action": "wait",
            "reason": "not_monthly_observation",
            "new_budget_fraction": 0.0,
        }
        stocks[name]["new_entry_eligible"] = bool(
            score is not None and score > 0 and stocks[name]["rising_sma200"] and name in known_vol
        )
    month_dates = [day for day in calendar if day.to_period("M") == as_of.to_period("M")]
    monthly = bool(month_dates and min(month_dates) == as_of)
    selected = []
    reference = float(np.median(list(known_vol.values()))) if known_vol else None
    insufficient_held = any(stocks[n]["quote_available"] and stocks[n]["sma200"] is None for n in held - pending)
    status = "monthly_observation" if monthly else "waiting_for_monthly_observation"
    if insufficient_held:
        status = "insufficient_held_history"
    if monthly and not insufficient_held:
        retained = [
            n for n in names if n in held - pending and (not stocks[n]["quote_available"] or stocks[n]["distance"] > 0)
        ]
        ranked = sorted(
            (n for n in names if n not in held and stocks[n]["new_entry_eligible"]),
            key=lambda n: (-stocks[n]["distance"], symbol_identity(n, market)),
        )
        selected = retained + ranked[: max(0, slots - len(retained))]
        for name, row in stocks.items():
            if name in retained:
                row.update(
                    action="hold",
                    reason="positive_stock_trend" if row["quote_available"] else "unobservable_held_reserves_slot",
                )
            elif name in held - pending:
                row.update(action="exit_next_valid_open", reason="monthly_sma200_nonpositive")
            elif name in selected:
                row.update(
                    action="entry_candidate_next_valid_open",
                    reason="rising_sma200_ranked_vacancy",
                    new_budget_fraction=ALLOCATION / slots * min(1.0, reference / known_vol[name]),
                )
            else:
                row["reason"] = "capacity_reserved" if row["new_entry_eligible"] else "entry_conditions_not_met"
    for name in pending:
        stocks[name].update(action="exit_pending", reason="previously_locked_exit")
    return {
        "model": MODEL,
        "validation_status": "not_validated",
        "kind": "monthly_signal_plan",
        "market": market,
        "as_of": str(as_of.date()),
        "status": status,
        "observation_date": str(as_of.date()) if monthly else None,
        "last_available_date": str(max(calendar).date()) if calendar else None,
        "portfolio_state": "explicit_held_symbols" if held else "initially_flat",
        "slots": slots,
        "allocation": ALLOCATION,
        "reference_volatility": reference,
        "selected": selected,
        "stocks": stocks,
        "execution": {
            "requires_available_cash_and_capacity": True,
            "budget_basis": "next_execution_batch_open_nav_times_new_budget_fraction_including_buy_costs",
            "budget_is_not_target_weight": True,
            "existing_holdings_are_not_resized": True,
            "sell_before_buy": True,
            "fills_or_quantities_calculated": False,
        },
        "validation": {
            "development_gates": "passed",
            "expanded_and_historical_pools": "failed",
            "independent_validation": "not_completed",
        },
        "warnings": [
            "Caller must provide complete daily history through a closed as_of bar, consistent adjusted prices and the real holdings at as_of. Missing pool members change ranking and the volatility median.",
            "Only the first observed positive-volume stock-pool close of the month creates new plans. Mid-month output does not replay past decisions or reconstruct orders.",
            "Budgets are conditional cash limits, not share quantities, target weights or fills. Locked exits require actual execution before cash/capacity is reused; no daily re-entry or averaging down.",
            "Corporate-action conversion, raw-share lots, settlement, trading limits and fees require separate execution checks. No account is stored and no orders are submitted.",
            "Development gates passed only on the development sample. Expanded and historical pools failed; allocation .8704 was fitted. This is not the private Trend Animal temperature or a forecast of fat-tail returns.",
        ],
    }


def latest_closed_date(market, now=None):
    import exchange_calendars as xcals

    calendar = xcals.get_calendar({"A": "XSHG", "HK": "XHKG", "US": "XNYS"}[market])
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Current time must include a timezone")
    local_date = str(now.tz_convert(calendar.tz).date())
    session = calendar.date_to_session(local_date, direction="previous")
    # XHKG calendars end continuous trading; its closing auction can run ten more minutes.
    close = calendar.session_close(session) + pd.Timedelta(minutes=10 if market == "HK" else 0)
    if now <= close:
        session = calendar.previous_session(session)
    return str(session.date())


def request_symbol(name):
    name = name.strip().upper()
    if re.fullmatch(r"\d{4,5}\.HK", name):
        return f"{int(name[:-3]):04d}.HK"
    return name.replace(".", "-") if detect_market(name) == "US" else name


def _fetch_daily_history(symbol, count):
    return run_tool("stock_data.py", ["kline", symbol, "--period", "daily", "--count", str(count)], timeout=30)


def fetch_plan(request):
    allowed = {"symbols", "as_of", "held_symbols", "pending_exit_symbols", "slots"}
    if not isinstance(request, dict) or set(request) - allowed:
        raise ValueError("Expected symbols and optional as_of/held_symbols/pending_exit_symbols/slots")
    symbols = request["symbols"]
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 100 or any(type(n) is not str for n in symbols):
        raise ValueError("symbols must be a nonempty list of stock codes")
    symbols = [request_symbol(n) for n in symbols]
    markets = {detect_market(n) for n in symbols}
    if len(markets) != 1:
        raise ValueError("A pool must contain a single market")
    market = markets.pop()
    names = pool_members(symbols, market)
    state = {}
    for key in ("held_symbols", "pending_exit_symbols"):
        values = request.get(key, [])
        if not isinstance(values, list) or any(type(n) is not str for n in values):
            raise ValueError("Held and pending symbols require lists of codes")
        state[key] = [request_symbol(n) for n in values]
    if "slots" in request:
        state["slots"] = request["slots"]
    pool_state(state, names)
    try:
        closed = latest_closed_date(market)
    except Exception:
        return {
            "error": "Exchange calendar unavailable; cannot identify a completed daily bar",
            "error_code": "calendar_unavailable",
        }
    as_of = iso_date(request.get("as_of", closed))
    if as_of > iso_date(closed):
        raise ValueError("as_of must not be later than the most recently completed exchange session")
    count = 360 + (iso_date(closed) - as_of).days
    if count > 20000 or count * len(names) > 200000:
        raise ValueError("Requested date exceeds the supported price history")
    histories, failed = {}, []
    with ThreadPoolExecutor(max_workers=min(6, len(names))) as pool:
        futures = {pool.submit(_fetch_daily_history, name, count): name for name in names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                records = future.result()
            except Exception:
                records = None
            if not isinstance(records, list) or not records or len(records) > count:
                failed.append(name)
            else:
                histories[name] = records
    if failed:
        return {
            "error": "Cannot load the complete stock pool; no strategy plan was produced",
            "error_code": "incomplete_pool",
            "failed_symbols": sorted(failed),
        }
    try:
        result = analyze({"market": market, "as_of": str(as_of.date()), "histories": histories, **state})
    except (KeyError, TypeError, ValueError, OverflowError):
        return {
            "error": "Fetched daily prices failed validation; no strategy plan was produced",
            "error_code": "invalid_market_data",
        }
    result["data_acquisition"] = {
        "source": "stock_data.py:kline",
        "symbols": names,
        "requested_count": count,
        "latest_closed_session": closed,
    }
    result["warnings"][0] = (
        "Daily histories were fetched for the entire requested pool. Verify provider adjustment and quote dates; held_symbols must describe real holdings at as_of."
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", nargs="?", choices=["fetch"], help="Fetch prices from stock codes; omit only for offline snapshots"
    )
    parser.add_argument("--input", required=True, help="Internal UTF-8 request JSON or offline snapshot file")
    args = parser.parse_args()
    try:
        path = Path(args.input)
        info = path.stat()
        limit = 32 * 1024 * 1024
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Input requires a regular JSON file up to 32 MiB")
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Input JSON exceeds 32 MiB")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        result = fetch_plan(payload) if args.action == "fetch" else analyze(payload)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        result = {
            "error": "Invalid stock-pool request; use one market and check codes, dates, holdings and slots"
            if args.action == "fetch"
            else "Invalid trend strategy input; check documented dates, symbols, prices, holdings and slots",
            "error_code": "invalid_request" if args.action == "fetch" else "invalid_snapshot",
        }
    except OSError:
        result = {"error": "Cannot read trend strategy input JSON file"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
