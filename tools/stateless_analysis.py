#!/usr/bin/env python3
"""Explicit-input research calculations. No account, watchlist or history storage."""

import argparse
import json
import math
import sys
from bisect import bisect_left
from copy import deepcopy
from datetime import date
from decimal import ROUND_DOWN, Decimal, localcontext
from difflib import SequenceMatcher
from fractions import Fraction
from pathlib import Path

from _subproc import utf8_stdio


class RawNumber(float):
    def __new__(cls, value):
        instance = super().__new__(cls, value)
        instance.raw = value
        return instance

    def __str__(self):
        return self.raw

    def __repr__(self):
        return self.raw


class JsonExactNumber:
    def __init__(self, raw):
        self.raw = raw


def int_to_decimal(value):
    if value.bit_length() < 14000 or not hasattr(sys, "set_int_max_str_digits"):
        return str(value)
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(0)
        return str(value)
    finally:
        sys.set_int_max_str_digits(previous)


def json_dumps_exact(value, cache=None):
    cache = {} if cache is None else cache
    if isinstance(value, RawNumber):
        return value.raw
    if isinstance(value, JsonExactNumber):
        return value.raw
    if isinstance(value, Fraction):
        return json_dumps_exact(fraction_to_json_number(value), cache)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                f"{json.dumps(key, ensure_ascii=False)}:{json_dumps_exact(item, cache)}" for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(json_dumps_exact(item, cache) for item in value) + "]"
    if isinstance(value, int) and not isinstance(value, bool):
        if value not in cache:
            cache[value] = int_to_decimal(value)
        return cache[value]
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def fraction_value(value):
    if isinstance(value, RawNumber):
        if len(value.raw) < 4000 and value.raw.lstrip("-").isdigit():
            return Fraction(int(value.raw))
        return Fraction(Decimal(value.raw))
    if isinstance(value, str):
        return Fraction(Decimal(value))
    if isinstance(value, (int, Decimal, Fraction)):
        return Fraction(value)
    return Fraction(Decimal(str(value)))


def fraction_decimal_places(value):
    denominator = value.denominator
    twos = (denominator & -denominator).bit_length() - 1
    remainder = denominator >> twos
    if remainder == 1:
        return twos
    estimate = int(remainder.bit_length() / 2.321928094887362)
    for fives in range(max(0, estimate - 2), estimate + 3):
        if 5**fives == remainder:
            return max(twos, fives)
    return None


def number(value, name, minimum=None, maximum=None):
    if isinstance(value, RawNumber):
        exact = fraction_value(value)
        if (
            minimum is not None
            and exact < fraction_value(minimum)
            or maximum is not None
            and exact > fraction_value(maximum)
        ):
            raise ValueError(f"{name} outside allowed range")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name} outside allowed range")
    return value


def exact_value(value):
    return fraction_value(value) if isinstance(value, RawNumber) else value


def positive(value, name):
    if isinstance(value, Fraction):
        if value <= 0:
            raise ValueError(f"{name} must be positive")
        return value
    value = number(value, name, minimum=0)
    if exact_value(value) == 0:
        raise ValueError(f"{name} must be positive")
    return value


def integer(value, name, minimum, maximum=None):
    value = number(value, name, minimum, maximum)
    if isinstance(value, RawNumber):
        exact = fraction_value(value)
        if exact.denominator != 1:
            raise ValueError(f"{name} must be an integer")
        return int(exact)
    if int(value) != value:
        raise ValueError(f"{name} must be an integer")
    return int(value)


def position_size(p):
    equity = positive(p["equity"], "equity")
    cash = number(p["cash"], "cash", 0, equity)
    entry = positive(p["entry_price"], "entry_price")
    stop = positive(p["stop_price"], "stop_price")
    if stop >= entry:
        raise ValueError("long position requires stop_price < entry_price")
    risk = number(p["risk_fraction"], "risk_fraction", 0, 1)
    cap = number(p.get("max_position_fraction", 1), "max_position_fraction", 0, 1)
    lot = integer(p["lot_size"], "lot_size", 1, 1000000)
    fee = number(p.get("fee_rate", 0), "fee_rate", 0, 0.1)
    slip = number(p.get("slippage_rate", 0), "slippage_rate", 0, 0.1)
    buy = entry * (1 + slip) * (1 + fee)
    sell = stop * (1 - slip) * (1 - fee)
    loss = buy - sell
    limits = {
        "risk": math.floor(equity * risk / loss / lot) * lot,
        "cash": math.floor(cash / buy / lot) * lot,
        "position": math.floor(equity * cap / buy / lot) * lot,
    }
    quantity = min(limits.values())
    return {
        "quantity": quantity,
        "risk_budget": equity * risk,
        "cash_required": round(quantity * buy, 6),
        "planned_loss": round(quantity * loss, 6),
        "position_fraction": quantity * buy / equity,
        "limits": limits,
        "binding_constraints": [key for key, value in limits.items() if value == quantity],
        "warnings": [
            "Long-only scenario; stop fills are not guaranteed. Rates exclude unprovided taxes/minimum fees; all amounts must share one currency."
        ],
    }


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def rows(value, name, minimum=1, maximum=100):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{name} has invalid row count")
    if not all(isinstance(row, dict) for row in value):
        raise ValueError(f"{name} must contain objects")
    return value


def prices(value):
    result = {}
    for row in rows(value, "prices", 2, 20000):
        day = date.fromisoformat(row["date"]).isoformat()
        if day != row["date"] or day in result:
            raise ValueError("price dates must be unique YYYY-MM-DD")
        result[day] = positive(row["close"], "close")
    return dict(sorted(result.items()))


def history_returns(history, common, cache):
    state = cache.get(id(history))
    if state is None:
        state = {"previous": {}, "adjacent": {}}
        for previous, day in zip(history, list(history)[1:]):
            state["previous"][day] = previous
            state["adjacent"][day] = (
                fraction_value(history[day]) - fraction_value(history[previous])
            ) / fraction_value(history[previous])
        cache[id(history)] = state
    values = []
    for previous, day in zip(common, common[1:]):
        if state["previous"].get(day) == previous:
            values.append(state["adjacent"][day])
        else:
            values.append(
                (fraction_value(history[day]) - fraction_value(history[previous])) / fraction_value(history[previous])
            )
    return values


def shock_map(value, key):
    result = {}
    for row in rows(value, f"{key}_shocks", 0):
        label = text(row[key], key)
        if label in result:
            raise ValueError(f"duplicate {key} shock")
        result[label] = number(row["shock"], "shock", -1, 1)
    return result


def stress(scenarios, positions, cash, base):
    results = []
    names = set()
    known = {
        "symbol": {row["symbol"] for row in positions},
        "sector": {row["sector"] for row in positions},
        "currency": {row["currency"] for row in positions},
    }
    for scenario in rows(scenarios, "stress_scenarios", 1, 20):
        if set(scenario) - {"name", "market_shock", "sector_shocks", "symbol_shocks", "currency_shocks"}:
            raise ValueError("unknown stress scenario field")
        name = text(scenario["name"], "stress scenario name")
        if name in names:
            raise ValueError("duplicate stress scenario name")
        names.add(name)
        market = number(scenario.get("market_shock", 0), "market_shock", -1, 1)
        sectors = shock_map(scenario.get("sector_shocks", []), "sector")
        symbols = shock_map(scenario.get("symbol_shocks", []), "symbol")
        currencies = shock_map(scenario.get("currency_shocks", []), "currency")
        if base in currencies:
            raise ValueError("base currency cannot be shocked against itself")
        for key, values in (("sector", sectors), ("symbol", symbols), ("currency", currencies)):
            if not set(values) <= known[key]:
                raise ValueError(f"stress scenario contains unknown {key}")
        rows_after, before_values, after_values, pnl_values = [], [], [], []
        for row in positions:
            symbol = row["symbol"]
            asset = symbols.get(symbol, sectors.get(row["sector"], market))
            fx = currencies.get(row["currency"], 0)
            before_value = fraction_value(row["value_base"])
            combined_shock = fraction_value(asset) + fraction_value(fx) + fraction_value(asset) * fraction_value(fx)
            pnl = before_value * combined_shock
            after = before_value + pnl
            before_values.append(before_value)
            after_values.append(after)
            pnl_values.append(pnl)
            rows_after.append(
                {
                    "symbol": symbol,
                    "asset_shock": asset,
                    "fx_shock": fx,
                    "value_before": fraction_to_json_number(before_value),
                    "value_after": fraction_to_json_number(after),
                    "pnl_base": fraction_to_json_number(pnl),
                }
            )
        before_total = fraction_value(cash) + sum(before_values, Fraction(0))
        after_total = fraction_value(cash) + sum(after_values, Fraction(0))
        pnl_total = sum(pnl_values, Fraction(0))
        results.append(
            {
                "name": name,
                "equity_before": fraction_to_json_number(before_total),
                "equity_after": fraction_to_json_number(after_total),
                "pnl_base": fraction_to_json_number(pnl_total),
                "return_pct": fraction_to_json_number(pnl_total / before_total * 100) if before_total else None,
                "positions": rows_after,
            }
        )
    return results


def currency_rates(p, base):
    rates = {base: 1}
    for row in rows(p.get("fx_rates", []), "fx_rates", 0):
        currency = text(row["currency"], "currency")
        if currency in rates:
            raise ValueError("duplicate/base FX currency")
        rates[currency] = positive(row["rate"], "rate")
    return rates


def portfolio(p):
    base = text(p["base_currency"], "base_currency")
    cash = number(p["cash"], "cash", 0)

    rates = currency_rates(p, base)
    positions = []
    currencies, sectors = {}, {}
    seen, dates = set(), set()
    pnl, stop_risk = 0, 0
    pnl_complete = stop_complete = True
    for row in rows(p["positions"], "positions"):
        symbol = text(row["symbol"], "symbol")
        if symbol in seen:
            raise ValueError("aggregate duplicate positions before analysis")
        seen.add(symbol)
        currency = text(row["currency"], "currency")
        rate = rates[currency]
        quantity = positive(row["quantity"], "quantity")
        price = positive(row["price"], "price")
        local_value = fraction_value(price) * fraction_value(quantity)
        value = local_value * fraction_value(rate)
        cost = positive(row["cost"], "cost") if row.get("cost") is not None else None
        stop = positive(row["stop_price"], "stop_price") if row.get("stop_price") is not None else None
        pnl_complete &= cost is not None
        stop_complete &= stop is not None
        if cost is not None:
            pnl += (fraction_value(price) - fraction_value(cost)) * fraction_value(quantity) * fraction_value(rate)
        if stop is not None:
            stop_risk += (
                max(fraction_value(price) - fraction_value(stop), Fraction(0))
                * fraction_value(quantity)
                * fraction_value(rate)
            )
        sector = text(row.get("sector", "unknown"), "sector")
        sectors.setdefault(sector, []).append(value)
        currencies.setdefault(currency, []).append(value)
        dates.add(text(row.get("as_of", "unknown"), "as_of"))
        positions.append({"symbol": symbol, "currency": currency, "sector": sector, "value_base": value})
    equity = fraction_value(cash) + sum((fraction_value(row["value_base"]) for row in positions), Fraction(0))
    for row in positions:
        row["weight"] = row["value_base"] / equity
    currencies = {
        key: sum((fraction_value(item) for item in values), Fraction(0)) for key, values in currencies.items()
    }
    sectors = {key: sum((fraction_value(item) for item in values), Fraction(0)) for key, values in sectors.items()}
    currencies[base] = currencies.get(base, Fraction(0)) + fraction_value(cash)

    correlations = []
    histories = {}
    for row in rows(p.get("histories", []), "histories", 0):
        if row["symbol"] not in seen or row["symbol"] in histories:
            raise ValueError("history symbol must match a unique position")
        histories[row["symbol"]] = prices(row["prices"])
    symbols = list(histories)
    returns_cache = {}
    history_data = {}
    if len(symbols) > 1:
        import numpy as np

        for history in histories.values():
            days = list(history)
            values = history_returns(history, days, returns_cache)
            center = values[0]
            deviations = [value - center for value in values]
            scale = max((abs(value) for value in deviations), default=0)
            raw_vector = np.array([float(value / scale) if scale else 0.0 for value in deviations])
            centered = raw_vector - raw_vector.mean()
            history_data[id(history)] = {
                "days": days,
                "days_array": np.array(days, dtype=str),
                "center": center,
                "scale": scale,
                "raw": raw_vector,
                "centered": centered,
                "norm": np.linalg.norm(centered),
            }

        def scaled_vector(history, common):
            data = history_data[id(history)]
            if common == data["days"]:
                return data["centered"], data["norm"]

            def exact_common_vector():
                values = history_returns(history, common, returns_cache)
                center = values[0]
                deviations = [value - center for value in values]
                scale = max((abs(value) for value in deviations), default=0)
                vector = np.array([float(value / scale) if scale else 0.0 for value in deviations])
                vector -= vector.mean()
                return vector, np.linalg.norm(vector)

            center, scale = data["center"], data["scale"]
            if scale == 0 or scale > 1000000 or scale < Fraction(1, 1000000):
                return exact_common_vector()
            indices = np.searchsorted(data["days_array"], common)
            subset = data["raw"][indices[1:] - 1].copy()
            for index in np.flatnonzero(np.diff(indices) != 1):
                gap = history_returns(history, [common[index], common[index + 1]], returns_cache)[0]
                try:
                    gap_value = float((gap - center) / scale)
                except OverflowError:
                    return exact_common_vector()
                if not math.isfinite(gap_value):
                    return exact_common_vector()
                subset[index] = gap_value
            if np.all(subset == subset[0]):
                return exact_common_vector()
            pre_demean_max_abs = float(np.max(np.abs(subset)))
            subset -= subset.mean()
            if not np.isfinite(subset).all():
                return exact_common_vector()
            norm = np.linalg.norm(subset)
            noise_floor = np.finfo(float).eps * max(1.0, len(subset) * pre_demean_max_abs)
            if norm <= noise_floor or not math.isfinite(norm):
                return exact_common_vector()
            return subset, norm

        def correlation_row(pair):
            a, b = pair
            common = [day for day in history_data[id(histories[a])]["days"] if day in histories[b]]
            corr = None
            if len(common) >= 4:
                x, x_norm = scaled_vector(histories[a], common)
                y, y_norm = scaled_vector(histories[b], common)
                if x_norm and y_norm:
                    correlation = float(np.dot(x, y) / (x_norm * y_norm))
                    corr = max(-1.0, min(1.0, correlation))
            return {"symbols": [a, b], "correlation": corr, "return_samples": max(len(common) - 1, 0)}

        pairs = [(a, b) for i, a in enumerate(symbols) for b in symbols[i + 1 :]]
        if pairs and max(len(history) for history in histories.values()) * len(pairs) > 200000:
            import os
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(2, os.cpu_count() or 1)) as pool:
                correlations = list(pool.map(correlation_row, pairs))
        else:
            correlations = [correlation_row(pair) for pair in pairs]
    warnings = [
        "Price P&L uses current FX, excludes historical FX P&L, fees and distributions; stop risk is a scenario, not a guaranteed loss limit.",
        "Correlations use local-price returns on common dates, exclude FX, and do not forecast future dependence.",
    ]
    if len(dates) != 1 or "unknown" in dates:
        warnings.append("Position valuation timestamps are missing or differ")
    result = {
        "base_currency": base,
        "total_equity": fraction_to_json_number(equity),
        "cash_weight": fraction_to_json_number(fraction_value(cash) / equity),
        "positions": positions,
        "price_pnl_base": pnl if pnl_complete else None,
        "stop_loss_risk_base": stop_risk if stop_complete else None,
        "currency_exposure": {k: v / equity for k, v in currencies.items()},
        "sector_exposure": {k: v / equity for k, v in sectors.items()},
        "position_hhi": sum(row["weight"] ** 2 for row in positions),
        "correlations": correlations,
        "warnings": warnings,
    }
    if "stress_scenarios" in p:
        result["stress_tests"] = stress(p["stress_scenarios"], positions, cash, base)
        warnings.append(
            "Stress scenarios are deterministic what-if calculations, not forecasts; symbol shocks override sector shocks, then market shock, and FX shocks compound separately."
        )
    return result


def fraction_to_json_number(value, rounding=None):
    value = fraction_value(value)
    if value.denominator == 1:
        return value.numerator
    places = fraction_decimal_places(value)
    if places is not None:
        scaled = value.numerator * (10**places // value.denominator)
        digits = int_to_decimal(abs(scaled)).rjust(places + 1, "0")
        sign = "-" if scaled < 0 else ""
        return JsonExactNumber(f"{sign}{digits[:-places]}.{digits[-places:]}" if places else f"{sign}{digits}")
    numerator_text = int_to_decimal(value.numerator)
    denominator_text = int_to_decimal(value.denominator)
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.Emax = max(ctx.Emax, len(numerator_text) + len(denominator_text) + 10)
        ctx.Emin = min(ctx.Emin, -ctx.Emax)
        if rounding is not None:
            ctx.rounding = rounding
        raw = format(Decimal(numerator_text) / Decimal(denominator_text), "e")
    return JsonExactNumber(raw)


def lot_parts(value, lot, divisor=1):
    """Return the exact-ratio ideal quantity and its lot-floor without binary boundary drift."""
    exact = fraction_value(value) / fraction_value(divisor)
    return exact, int(exact / lot) * lot


def rebalance(p):
    base = text(p["base_currency"], "base_currency")
    cash = number(p["cash"], "cash", 0)
    rates = currency_rates(p, base)
    fee = number(p.get("fee_rate", 0), "fee_rate", 0, 0.1)
    slip = number(p.get("slippage_rate", 0), "slippage_rate", 0, 0.1)
    turnover_limit = (
        number(p["max_turnover_fraction"], "max_turnover_fraction", 0) if "max_turnover_fraction" in p else None
    )
    current, dates = {}, set()
    for row in rows(p["positions"], "positions", 0):
        symbol = text(row["symbol"], "symbol")
        if symbol in current:
            raise ValueError("duplicate current position")
        currency = text(row["currency"], "currency")
        as_of = text(row.get("as_of", "unknown"), "as_of")
        current[symbol] = {
            "symbol": symbol,
            "quantity": positive(row["quantity"], "quantity"),
            "price": positive(row["price"], "price"),
            "currency": currency,
            "rate": rates[currency],
            "sector": text(row.get("sector", "unknown"), "sector"),
            "as_of": as_of,
        }
        dates.add(as_of)
    targets = {}
    for row in rows(p["targets"], "targets", 1):
        symbol = text(row["symbol"], "symbol")
        if symbol in targets:
            raise ValueError("duplicate rebalance target")
        old = current.get(symbol, {})
        weight = number(row["weight"], "target weight", 0, 1)
        currency = text(row.get("currency", old.get("currency")), "currency")
        as_of = text(row.get("as_of", old.get("as_of", "unknown")), "as_of")
        targets[symbol] = {
            "symbol": symbol,
            "weight": weight,
            "price": positive(row.get("price", old.get("price")), "price"),
            "currency": currency,
            "rate": rates[currency],
            "sector": text(row.get("sector", old.get("sector", "unknown")), "sector"),
            "as_of": as_of,
            "lot_size": integer(row["lot_size"], "lot_size", 1)
            if "lot_size" in row or exact_value(weight) > 0
            else None,
        }
        dates.add(as_of)
    if sum((fraction_value(row["weight"]) for row in targets.values()), Fraction(0)) > 1:
        raise ValueError("target weights must sum to at most 1")
    for symbol, old in current.items():
        if symbol not in targets:
            targets[symbol] = {**old, "weight": 0, "lot_size": None}
    equity_fraction = fraction_value(cash) + sum(
        (
            fraction_value(current[symbol]["quantity"])
            * fraction_value(targets[symbol]["price"])
            * fraction_value(targets[symbol]["rate"])
            for symbol in current
        ),
        Fraction(0),
    )
    for row in targets.values():
        row["unit_value"] = fraction_value(row["price"]) * fraction_value(row["rate"])
        if exact_value(row["weight"]) == 0:
            row["ideal_quantity"] = row["target_quantity"] = 0
            continue
        positive(equity_fraction * fraction_value(row["weight"]), "target value")
        positive(row["unit_value"], "target unit value")
        row["ideal_quantity"], row["target_quantity"] = lot_parts(
            fraction_value(equity_fraction) * fraction_value(row["weight"]), row["lot_size"], row["unit_value"]
        )
        if row["target_quantity"] == 0:
            raise ValueError("positive target weight is below one lot")

    sells, buys, unfilled = [], [], []
    for symbol, row in sorted(targets.items()):
        held = current.get(symbol, {}).get("quantity", 0)
        raw_delta = row["ideal_quantity"] - fraction_value(held)
        side = "buy" if raw_delta > 0 else "sell"
        raw_desired = abs(raw_delta)
        if raw_desired == 0:
            quantity = 0
        elif side == "sell" and exact_value(row["weight"]) == 0:
            quantity = fraction_value(held)
        else:
            _, quantity = lot_parts(raw_desired, row["lot_size"])
        if raw_desired - fraction_value(quantity) > 0:
            unfilled.append(
                {
                    "symbol": symbol,
                    "side": "buy" if raw_delta > 0 else "sell",
                    "quantity": fraction_to_json_number(raw_desired - fraction_value(quantity), rounding=ROUND_DOWN),
                    "reason": "lot_rounding",
                }
            )
        if quantity:
            order = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "requested_quantity": quantity,
                "lot_size": row["lot_size"],
                "price": row["price"],
                "currency": row["currency"],
                "unit_value": row["unit_value"],
            }
            (buys if side == "buy" else sells).append(order)

    def cash_flow(order):
        gross = fraction_value(order["quantity"]) * fraction_value(order["unit_value"])
        if order["side"] == "sell":
            return gross * (1 - fraction_value(slip)) * (1 - fraction_value(fee))
        return -gross * (1 + fraction_value(slip)) * (1 + fraction_value(fee))

    available = fraction_value(cash) + sum((cash_flow(order) for order in sells), Fraction(0))
    desired_buy_cost = -sum((cash_flow(order) for order in buys), Fraction(0))
    cash_limited = desired_buy_cost > available
    if cash_limited and desired_buy_cost:
        scale = max(Fraction(0), available / desired_buy_cost)
        for order in buys:
            _, quantity = lot_parts(order["requested_quantity"] * scale, order["lot_size"])
            if quantity < order["requested_quantity"]:
                unfilled.append(
                    {
                        "symbol": order["symbol"],
                        "side": "buy",
                        "quantity": order["requested_quantity"] - quantity,
                        "reason": "cash",
                    }
                )
            order["quantity"] = quantity
    gross = sum(
        (fraction_value(order["quantity"]) * fraction_value(order["unit_value"]) for order in sells + buys),
        Fraction(0),
    )
    if equity_fraction == 0 and gross:
        raise ValueError("turnover requires positive equity")
    gross_fraction = gross / equity_fraction if equity_fraction else Fraction(0)
    if turnover_limit is not None and gross_fraction > fraction_value(turnover_limit):
        return {
            "error": "Rebalance exceeds max_turnover_fraction",
            "error_code": "turnover_limit_exceeded",
            "required_turnover_fraction": fraction_to_json_number(gross_fraction),
            "max_turnover_fraction": turnover_limit,
        }

    post_quantities = {symbol: fraction_value(row["quantity"]) for symbol, row in current.items()}
    for order in sells + buys:
        change = order["quantity"] * (1 if order["side"] == "buy" else -1)
        post_quantities[order["symbol"]] = post_quantities.get(order["symbol"], 0) + change
    cash_after = available + sum((cash_flow(order) for order in buys), Fraction(0))
    if cash_after < 0:
        raise ValueError("rebalance cash reconciliation failed")
    positions_after = []
    for symbol, row in sorted(targets.items()):
        quantity = post_quantities.get(symbol, 0)
        if quantity <= 0:
            continue
        positions_after.append(
            {
                "symbol": symbol,
                "quantity": fraction_to_json_number(quantity),
                "price": row["price"],
                "currency": row["currency"],
                "sector": row["sector"],
                "as_of": row["as_of"],
                "target_weight": row["weight"],
                "value_base": fraction_value(quantity) * row["unit_value"],
            }
        )
    equity_after = cash_after + sum((row["value_base"] for row in positions_after), Fraction(0))
    for row in positions_after:
        row["weight"] = fraction_to_json_number(row["value_base"] / equity_after)
        row["value_base"] = fraction_to_json_number(row["value_base"])
    orders = []
    for order in sells + buys:
        if not order["quantity"]:
            continue
        orders.append(
            {
                "symbol": order["symbol"],
                "side": order["side"],
                "quantity": fraction_to_json_number(order["quantity"]),
                "reference_price": order["price"],
                "currency": order["currency"],
                "lot_size": order["lot_size"],
                "estimated_value_base": fraction_to_json_number(
                    fraction_value(order["quantity"]) * fraction_value(order["unit_value"])
                ),
                "estimated_cash_flow_base": fraction_to_json_number(cash_flow(order)),
            }
        )
    warnings = [
        "Reference-price plan only; sells are ordered before buys. Actual fills, unprovided taxes/minimum fees, settlement and liquidity require confirmation."
    ]
    if len(dates) != 1 or "unknown" in dates:
        warnings.append("Position and target valuation timestamps are missing or differ")
    if cash_limited:
        warnings.append("Cash was insufficient for all target buys; buy quantities were reduced proportionally by lot")
    return {
        "base_currency": base,
        "initial_equity": fraction_to_json_number(equity_fraction),
        "equity_after": fraction_to_json_number(equity_after),
        "cash_after": fraction_to_json_number(cash_after),
        "cash_weight_after": fraction_to_json_number(cash_after / equity_after) if equity_after else None,
        "gross_turnover_fraction": fraction_to_json_number(gross_fraction),
        "turnover_basis": "gross reference buy plus sell value / initial equity",
        "estimated_cost_base": fraction_to_json_number(equity_fraction - equity_after),
        "cash_limited": cash_limited,
        "orders": orders,
        "unfilled": unfilled,
        "positions_after": positions_after,
        "warnings": warnings,
    }


def metric_map(company):
    result = {}
    for metric in rows(company["metrics"], "metrics", 0):
        name = text(metric["name"], "metric name")
        if name in result:
            raise ValueError("duplicate metric name")
        value = metric.get("value")
        if value is not None:
            number(value, "metric value")
        result[name] = {
            "value": value,
            "unit": text(metric["unit"], "unit"),
            "basis": text(metric["basis"], "basis"),
            "period": text(company["period"], "period"),
            "source": metric.get("source"),
        }
    return result


def peers(p):
    companies = {}
    for row in rows(p["companies"], "companies", 2, 30):
        symbol = text(row["symbol"], "symbol")
        if symbol in companies:
            raise ValueError("duplicate company")
        companies[symbol] = metric_map(row)
    metrics = {}
    names = sorted({name for company in companies.values() for name in company})
    for name in names:
        values = {
            symbol: company[name]
            for symbol, company in companies.items()
            if name in company and company[name]["value"] is not None
        }
        bases = {(v["unit"], v["basis"], v["period"]) for v in values.values()}
        comparable = len(values) >= 2 and len(bases) == 1
        metrics[name] = {
            "values": values,
            "missing": [symbol for symbol in companies if symbol not in values],
            "comparable": comparable,
            "ascending": sorted(values, key=lambda symbol: values[symbol]["value"]) if comparable else None,
            "warning": None if comparable else "Need two values with identical unit, basis and period",
        }
    return {
        "metrics": metrics,
        "warnings": [
            "Ascending order is not an investment ranking. Currency amounts require explicit currency in unit; no automatic currency or accounting conversion."
        ],
    }


def disclosure(p):
    current, previous = p["current"], p["previous"]
    for document in (current, previous):
        text(document["source"], "source")
        body = text(document["text"], "text")
        if len(body) > 200000:
            raise ValueError("document exceeds 200000 characters")
    old, new = previous["text"].splitlines(), current["text"].splitlines()
    changes = []
    limit = integer(p.get("max_changes", 30), "max_changes", 1, 100)
    for tag, i, j, a, b in SequenceMatcher(None, old, new).get_opcodes():
        if tag == "equal":
            continue
        item = {"type": tag}
        for key, document, lines, start, end in (("previous", previous, old, i, j), ("current", current, new, a, b)):
            excerpt = "\n".join(lines[start:end])
            item[key] = {
                "source": document["source"],
                "lines": [start + 1, end] if end > start else None,
                "text": excerpt[:2000],
                "truncated": len(excerpt) > 2000,
            }
        changes.append(item)
    before, after = metric_map(previous), metric_map(current)
    metric_changes = {}
    for name in sorted(before.keys() | after.keys()):
        x, y = before.get(name), after.get(name)
        comparable = bool(
            x
            and y
            and x["value"] is not None
            and y["value"] is not None
            and x["unit"] == y["unit"]
            and x["basis"] == y["basis"]
        )
        metric_changes[name] = {
            "previous": x,
            "current": y,
            "comparable": comparable,
            "delta": y["value"] - x["value"] if comparable else None,
            "growth_pct": (y["value"] / x["value"] - 1) * 100 if comparable and x["value"] > 0 else None,
        }
    return {
        "text_changes": changes[:limit],
        "total_changes": len(changes),
        "truncated": len(changes) > limit,
        "metric_changes": metric_changes,
        "warnings": [
            "Text changes are evidence, not economic conclusions. Inputs must be verified full text; OCR and PDF extraction are performed by the host. Metric comparability relies on caller-provided accounting basis and units; nonpositive prior values have no ordinary growth rate."
        ],
    }


def snapshot(p):
    if not isinstance(p["previous"], dict) or not isinstance(p["current"], dict):
        raise ValueError("snapshots must be objects")
    changes = []
    missing = object()

    def unavailable(value):
        return value is None or isinstance(value, dict) and "error" in value

    def compare(old, new, path):
        if old is missing:
            status = "added"
        elif new is missing:
            status = "removed"
        elif unavailable(new) and not unavailable(old):
            status = "unavailable"
        elif unavailable(old) and not unavailable(new):
            status = "recovered"
        elif isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() | new.keys()):
                escaped = key.replace("~", "~0").replace("/", "~1")
                compare(old.get(key, missing), new.get(key, missing), f"{path}/{escaped}")
            return
        elif type(old) is type(new) and old == new:
            return
        else:
            status = "changed"
        changes.append(
            {
                "path": path,
                "status": status,
                "previous": None if old is missing else old,
                "current": None if new is missing else new,
            }
        )

    compare(p["previous"], p["current"], "")
    return {
        "changes": changes,
        "warnings": [
            "Lists are compared as whole values; align symbols and normalize units/timestamps before comparing. Removed keys describe input structure, not confirmed business events."
        ],
    }


def fetch_dataset(symbol, p):
    from backtest import fetch_kline

    symbol = text(symbol, "symbol")
    start = date.fromisoformat(p["start_date"]).isoformat()
    end = date.fromisoformat(p["end_date"]).isoformat()
    if start != p["start_date"] or end != p["end_date"] or start >= end:
        raise ValueError("start_date/end_date must be ordered YYYY-MM-DD")
    frame = fetch_kline(symbol, start, end)
    if frame.empty:
        raise ValueError("No price data in requested range")
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    bars = frame.to_dict("records")
    basis = sorted({bar["adjusted"] for bar in bars if isinstance(bar.get("adjusted"), str) and bar["adjusted"]})
    return {
        "symbol": symbol,
        "bars": bars,
        "price_basis": ",".join(basis) if basis else "unspecified_provider_basis",
        "coverage": {
            "requested_start": start,
            "requested_end": end,
            "actual_start": bars[0]["date"],
            "actual_end": bars[-1]["date"],
        },
    }


def event_study(p):
    coverage = None
    if "symbol" in p or "benchmark" in p:
        from stock_data import detect_market

        if "prices" in p or "benchmark_prices" in p:
            raise ValueError("choose symbols or explicit prices, not both")
        if detect_market(p["symbol"]) != detect_market(p["benchmark"]):
            raise ValueError("stock and benchmark must belong to the same market")
        loaded = fetch_dataset(p["symbol"], p)
        benchmark = fetch_dataset(p["benchmark"], p)
        coverage = {"stock": loaded["coverage"], "benchmark": benchmark["coverage"]}
        p = {
            **p,
            "prices": loaded["bars"],
            "benchmark_prices": benchmark["bars"],
            "price_basis": f"stock:{loaded['price_basis']}; benchmark:{benchmark['price_basis']}",
        }
    stock, benchmark = prices(p["prices"]), prices(p["benchmark_prices"])
    text(p["price_basis"], "price_basis")
    horizons = p.get("horizons", [0, 1, 5, 10, 20])
    if not isinstance(horizons, list) or not 1 <= len(horizons) <= 20:
        raise ValueError("horizons must contain 1..20 integers")
    horizons = sorted({integer(h, "horizon", 0, 250) for h in horizons})
    dates = list(stock)
    event_dates = p["event_dates"]
    if (
        not isinstance(event_dates, list)
        or not 1 <= len(event_dates) <= 100
        or len(set(event_dates)) != len(event_dates)
    ):
        raise ValueError("event_dates must be unique, 1..100 dates")
    events, intervals = [], []
    for event_date in sorted(event_dates):
        if date.fromisoformat(event_date).isoformat() != event_date:
            raise ValueError("event date must be YYYY-MM-DD")
        idx = bisect_left(dates, event_date)
        event = {"event_date": event_date, "effective_date": dates[idx] if idx < len(dates) else None, "windows": []}
        for horizon in horizons:
            end = idx + horizon
            window = {
                "horizon": horizon,
                "return_pct": None,
                "benchmark_return_pct": None,
                "excess_return_pct": None,
                "reason": None,
            }
            if idx == 0 or end >= len(dates):
                window["reason"] = "insufficient_stock_window"
            else:
                start_date, end_date = dates[idx - 1], dates[end]
                window.update(
                    {
                        "start_date": start_date,
                        "end_date": end_date,
                        "return_pct": (stock[end_date] / stock[start_date] - 1) * 100,
                    }
                )
                if start_date not in benchmark or end_date not in benchmark:
                    window["reason"] = "missing_benchmark_endpoint"
                else:
                    window["benchmark_return_pct"] = (benchmark[end_date] / benchmark[start_date] - 1) * 100
                    window["excess_return_pct"] = window["return_pct"] - window["benchmark_return_pct"]
            event["windows"].append(window)
        valid_horizons = [window["horizon"] for window in event["windows"] if window["excess_return_pct"] is not None]
        intervals.append((idx - 1, idx + max(valid_horizons)) if valid_horizons else None)
        events.append(event)
    for i, event in enumerate(events):
        interval = intervals[i]
        if interval is None:
            event["overlaps_other_event"] = False
            continue
        a, b = interval
        event["overlaps_other_event"] = any(
            other is not None and max(a, other[0]) < min(b, other[1]) for j, other in enumerate(intervals) if i != j
        )
    summary = {}
    for horizon in horizons:
        values = [
            w["excess_return_pct"]
            for e in events
            for w in e["windows"]
            if w["horizon"] == horizon and w["excess_return_pct"] is not None
        ]
        summary[str(horizon)] = {
            "samples": len(values),
            "mean_excess_return_pct": sum(values) / len(values) if values else None,
            "positive_fraction": sum(v > 0 for v in values) / len(values) if values else None,
        }
    return {
        "price_basis": p["price_basis"],
        "data_coverage": coverage,
        "events": events,
        "summary": summary,
        "warnings": [
            "Event day is the first supplied stock trading date on/after the date; use the next session for after-close releases. Baseline is its prior close. Verify complete, consistently adjusted same-market series; missing rows are not detected as holidays. Excess return is stock minus benchmark buy-and-hold return, not causal attribution. Overlapping events are not independent samples."
        ],
    }


def robustness(p):
    import pandas as pd
    from backtest import CONDITION_OPERATORS, _resolve_refs, compute_metrics, get_indicator, simulate

    capital = positive(p.get("capital", 1000000), "capital")
    train = integer(p.get("train_bars", 126), "train_bars", 30, 1000)
    test = integer(p.get("test_bars", 63), "test_bars", 30, 1000)
    strategies, strategy_errors = {}, {}
    for row in rows(p["strategies"], "strategies", 1, 5):
        name = text(row["name"], "strategy name")
        if name in strategies:
            raise ValueError("strategy must have a unique name")
        strategy = deepcopy(row["strategy"])
        strategies[name] = strategy
        try:
            if not isinstance(strategy, dict) or any(
                not isinstance(strategy.get(field, {}), dict)
                for field in ("parameters", "simulation", "entry", "exit", "position")
            ):
                raise ValueError("strategy sections must be objects")
            _resolve_refs(strategy, strategy.get("parameters", {}))
            if strategy.get("simulation", {}).get("execution", "next_open") != "next_open":
                raise ValueError("robustness requires next_open execution")
        except (ValueError, TypeError, KeyError, OverflowError):
            strategy_errors[name] = "Invalid strategy structure or execution model"
    if "symbols" in p:
        symbols = p["symbols"]
        if "datasets" in p or not isinstance(symbols, list) or not 1 <= len(symbols) <= 5:
            raise ValueError("choose 1..5 symbols or explicit datasets, not both")
        if len(set(symbols)) != len(symbols):
            raise ValueError("duplicate symbols")
        datasets = [fetch_dataset(symbol, p) for symbol in symbols]
    else:
        datasets = p["datasets"]
    results, seen = [], set()
    for dataset in rows(datasets, "datasets", 1, 5):
        symbol = text(dataset["symbol"], "symbol")
        if symbol in seen:
            raise ValueError("duplicate dataset symbol")
        seen.add(symbol)
        text(dataset["price_basis"], "price_basis")
        bars = rows(dataset["bars"], "bars", train + test, 2000)
        prices(bars)
        if [bar["date"] for bar in bars] != sorted(bar["date"] for bar in bars):
            raise ValueError("bars must be ordered by date")
        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        for field in ("open", "high", "low", "close", "volume"):
            for value in df[field]:
                number(value, field, 0)
        if (df[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError("OHLC must be positive")
        if ((df["low"] > df[["open", "close"]].min(axis=1)) | (df["high"] < df[["open", "close"]].max(axis=1))).any():
            raise ValueError("inconsistent OHLC")

        def evaluate(frame, name, symbol=symbol):
            if name in strategy_errors:
                return {"name": name, "error": strategy_errors[name]}
            try:
                strategy = strategies[name]
                for side in ("entry", "exit"):
                    config = strategy.get(side, {})
                    if config.get("logic", "all" if side == "entry" else "any") not in ("all", "any"):
                        raise ValueError("condition logic must be all or any")
                    for condition in rows(config.get("conditions", []), "conditions", 0):
                        indicator = text(condition["indicator"], "indicator")
                        if condition["operator"] not in CONDITION_OPERATORS:
                            raise ValueError("unknown condition operator")
                        periods = [
                            integer(condition[field], field, 1, 1000000) if field in condition else None
                            for field in ("period", "fast_period", "slow_period")
                        ]
                        get_indicator(frame, indicator, *periods)
                        number(float(condition["value"]), "condition value")
                    for field in ("stop_loss", "take_profit", "trailing_stop"):
                        if config.get(field) is not None:
                            number(float(config[field]), field)
                sim = simulate(frame.reset_index(drop=True), strategy, capital, symbol)
                start, end = (frame["date"].iloc[i].strftime("%Y-%m-%d") for i in (0, -1))
                metrics = compute_metrics(sim["trades"], sim["equity_curve"], capital, sim["final_equity"], start, end)
                if not math.isfinite(metrics["profit_loss_ratio"]):
                    metrics["profit_loss_ratio"] = None
                return {
                    "name": name,
                    "metrics": metrics,
                    "final_equity": sim["final_equity"],
                    "execution": sim["execution"],
                    "costs": sim["costs"],
                }
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                return {"name": name, "error": "Invalid strategy or incompatible data"}

        folds = []
        for end_train in range(train, len(df) - test + 1, test):
            training = df.iloc[end_train - train : end_train]
            testing = df.iloc[end_train : end_train + test]
            candidates = [evaluate(training, name) for name in strategies]
            valid = [row for row in candidates if "error" not in row]
            selected = max(valid, key=lambda row: row["final_equity"])["name"] if valid else None
            folds.append(
                {
                    "train_start": training["date"].iloc[0].strftime("%Y-%m-%d"),
                    "train_end": training["date"].iloc[-1].strftime("%Y-%m-%d"),
                    "test_start": testing["date"].iloc[0].strftime("%Y-%m-%d"),
                    "test_end": testing["date"].iloc[-1].strftime("%Y-%m-%d"),
                    "candidates": candidates,
                    "selected": selected,
                    "out_of_sample": evaluate(testing, selected)
                    if selected
                    else {"error": "No valid training candidate"},
                }
            )
        results.append(
            {
                "symbol": symbol,
                "price_basis": dataset["price_basis"],
                "data_coverage": dataset.get("coverage"),
                "folds": folds,
                "sensitivity": [evaluate(df, name) for name in strategies],
                "unused_tail_bars": (len(df) - train) % test,
            }
        )
    return {
        "selection_metric": "training total_return",
        "datasets": results,
        "warnings": [
            "Each fold starts in cash with independently initialized indicators; windows are not a continuous portfolio. Training selects only supplied candidates; full-window sensitivity is in-sample, not validation. Per-symbol selection is independent; no automatic universe selection. Supplied adjusted OHLC, costs, corporate actions and historical price-limit limitations of run_backtest still apply. Missing trading dates and survivorship bias cannot be verified from input alone."
        ],
    }


COMMANDS = {
    "position_size": position_size,
    "portfolio": portfolio,
    "rebalance": rebalance,
    "peers": peers,
    "disclosure": disclosure,
    "snapshot": snapshot,
    "event_study": event_study,
    "robustness": robustness,
}


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"error": "Invalid command arguments; see --help"}))
        raise SystemExit(2)


def main():
    utf8_stdio()
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--input", help="JSON file; otherwise read stdin")
    args = parser.parse_args()
    try:
        raw = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()

        def reject_constant(_value):
            raise ValueError("JSON numbers must be finite")

        payload = json.loads(
            raw,
            parse_constant=reject_constant,
            parse_float=RawNumber
            if args.command in {"rebalance", "portfolio"}
            else lambda value: number(float(value), "JSON number"),
            parse_int=RawNumber if args.command in {"rebalance", "portfolio"} else None,
        )
        if not isinstance(payload, dict):
            raise ValueError("input must be a JSON object")
        result = COMMANDS[args.command](payload)
        output = (
            json_dumps_exact(result)
            if args.command in {"rebalance", "portfolio"}
            else json.dumps(result, ensure_ascii=False, allow_nan=False)
        )
        output.encode("utf-8")
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, OSError):
        result = {
            "error": "Invalid input: check required fields, types, finite values, dates and documented constraints"
        }
        output = json.dumps(result)
    except Exception:
        result = {"error": "Analysis failed; check input and installed dependencies"}
        output = json.dumps(result)
    print(output)
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
