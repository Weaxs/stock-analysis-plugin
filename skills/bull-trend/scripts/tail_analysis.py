#!/usr/bin/env python3
"""Skill-local tail and asymmetric-risk diagnostics from returns, backtest trades or payoff scenarios."""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Input values must be finite JSON numbers")
    return float(value)


def ratio(numerator, denominator):
    return numerator / denominator if denominator > 0 else None


def return_asymmetry(values, tail_prob, threshold):
    lower, upper = (float(q) for q in np.quantile(values, [tail_prob, 1 - tail_prob]))
    lower_tail, upper_tail = values[values <= lower], values[values >= upper]
    tail_loss, tail_gain = float(lower_tail.mean()), float(upper_tail.mean())
    excess = values - threshold
    return {
        "tail_probability": tail_prob,
        "threshold": threshold,
        "lower_quantile": lower,
        "upper_quantile": upper,
        "expected_tail_loss": tail_loss,
        "expected_tail_gain": tail_gain,
        "tail_gain_loss_ratio": ratio(tail_gain, -tail_loss) if tail_gain > 0 else None,
        "lower_tail_count": len(lower_tail),
        "upper_tail_count": len(upper_tail),
        "omega": ratio(float(np.clip(excess, 0, None).sum()), float(np.clip(-excess, 0, None).sum())),
    }


def analyze_returns(data, args):
    if not isinstance(data, list) or len(data) < 2:
        raise ValueError("Expected a JSON array of at least two historical returns")
    values = np.array([finite_number(value) for value in data])
    if not 100 <= args.draws <= 1000000 or args.seed < 0 or any(n < 2 or n > 10000 for n in args.n):
        raise ValueError("Require draws in 100..1000000, seed >= 0, and n in 2..10000")
    if not 0 < args.tail_prob < 0.5 or not math.isfinite(args.threshold):
        raise ValueError("Require tail-prob in (0, 0.5) and a finite threshold")
    centered = values - values.mean()
    mad = float(np.abs(centered).mean())
    rng = np.random.default_rng(args.seed)
    rows = []
    for n in args.n:
        total = 0.0
        if mad > 0:
            # Bound temporary arrays when either summands or draws is large.
            batch_size = max(1, 1000000 // n)
            for start in range(0, args.draws, batch_size):
                batch = min(batch_size, args.draws - start)
                sums = rng.choice(centered, size=(batch, n)).sum(axis=1)
                total += float(np.abs(sums).sum())
        summed_mad = total / args.draws
        denominator = math.log(summed_mad / mad) if mad > 0 and summed_mad > 0 else 0
        rows.append(
            {
                "n0": 1,
                "n": n,
                "mean_absolute_deviation_sum": summed_mad,
                "kappa": 2 - math.log(n) / denominator if denominator else None,
                "unavailable_reason": None if denominator else "Zero dispersion or degenerate aggregation",
            }
        )
    event = None
    if args.current is not None:
        current = finite_number(args.current)
        event = {
            "return": current,
            "percentile": float(np.mean(values <= current)),
            "upper_exceedances": int(np.sum(values > current)),
            "lower_exceedances": int(np.sum(values < current)),
        }
    return {
        "sample_count": len(values),
        "draws": args.draws,
        "seed": args.seed,
        "mean_absolute_deviation": mad,
        "kappa": rows,
        "current_event": event,
        "asymmetry": return_asymmetry(values, args.tail_prob, args.threshold),
        "method": "iid resampling of centered empirical returns; not a population tail estimate or trading signal",
    }


def analyze_trades(data):
    if not isinstance(data, dict) or not isinstance(data.get("trades"), list):
        raise ValueError("Expected a backtest JSON object with a trades array")
    if any(not isinstance(trade, dict) or trade.get("type") not in {"buy", "sell"} for trade in data["trades"]):
        raise ValueError("Each trade must have type buy or sell")
    pnl = [finite_number(trade.get("pnl")) for trade in data["trades"] if trade["type"] == "sell"]
    winners = sorted((value for value in pnl if value > 0), reverse=True)
    losers = [value for value in pnl if value < 0]
    gross_profit = math.fsum(winners)
    gross_loss = math.fsum(losers)
    net = math.fsum(pnl)
    payoff = ratio(gross_profit / len(winners), -gross_loss / len(losers)) if winners and losers else None
    return {
        "closed_trades": len(pnl),
        "winning_trades": len(winners),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "realized_net_pnl": net,
        "worst_trade_pnl": min(pnl) if pnl else None,
        "win_rate": len(winners) / len(pnl) if pnl else None,
        "payoff_ratio": payoff,
        "breakeven_win_rate": 1 / (1 + payoff) if payoff else None,
        "winner_contribution": [
            {
                "requested_k": k,
                "actual_k": min(k, len(winners)),
                "share_of_gross_profit": math.fsum(winners[:k]) / gross_profit if gross_profit else None,
                "realized_pnl_without_top_k": net - math.fsum(winners[:k]),
            }
            for k in (1, 3)
        ],
        "method": "closed monetary pnl only; deletion sensitivity is not an equity-curve rerun",
    }


def analyze_fragility(data, center):
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise ValueError("Expected a JSON object with a scenarios array of {shock, pnl}")
    capital = data.get("capital")
    if capital is not None and finite_number(capital) <= 0:
        raise ValueError("capital must be positive")
    payoff = {}
    for row in data["scenarios"]:
        if not isinstance(row, dict):
            raise ValueError("Each scenario must be an object with shock and pnl")
        # Rounded keys pair center±delta despite binary float error.
        shock = round(finite_number(row.get("shock")), 10)
        if shock in payoff:
            raise ValueError("Duplicate scenario shock")
        payoff[shock] = finite_number(row.get("pnl"))
    center = round(finite_number(center), 10)
    if center not in payoff:
        raise ValueError("Scenarios must include the center shock")
    base, pairs = payoff[center], []
    for shock in sorted(s for s in payoff if s > center):
        delta = round(shock - center, 10)
        down = payoff.get(round(center - delta, 10))
        if down is None:
            continue
        up = payoff[shock]
        h = (up + down) / 2 - base
        scale = max(1.0, abs(up), abs(down), abs(base))
        pairs.append(
            {
                "delta": delta,
                "pnl_down": down,
                "pnl_center": base,
                "pnl_up": up,
                "h": h,
                "h_over_capital": h / capital if capital else None,
                "gain_loss_ratio": ratio(up - base, base - down),
                "shape": "linear" if abs(h) <= 1e-9 * scale else "convex" if h > 0 else "concave",
            }
        )
    if not pairs:
        raise ValueError("Need at least one center-delta and center+delta scenario pair")
    return {
        "center": center,
        "pairs": pairs,
        "method": "H=(f(center-delta)+f(center+delta))/2-f(center) on supplied scenarios; H<0 is fragile",
    }


def main():
    try:
        parser = Parser(description=__doc__)
        sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
        returns = sub.add_parser("returns", help="Analyze a JSON array of historical log returns")
        returns.add_argument("input", type=Path)
        returns.add_argument("--n", nargs="+", type=int, default=[2, 10, 30])
        returns.add_argument("--draws", type=int, default=20000)
        returns.add_argument("--seed", type=int, default=42)
        returns.add_argument("--current", type=float, help="Separate current return, excluded from historical input")
        returns.add_argument("--tail-prob", type=float, default=0.05, help="Each tail's probability for asymmetry")
        returns.add_argument("--threshold", type=float, default=0.0, help="Omega gain/loss threshold return")
        trades = sub.add_parser("trades", help="Analyze a complete run_backtest JSON result")
        trades.add_argument("input", type=Path)
        fragility = sub.add_parser("fragility", help="Taleb-IMF fragility heuristic from payoff scenarios")
        fragility.add_argument("input", type=Path)
        fragility.add_argument("--center", type=float, default=0.0, help="Shock level to perturb around")
        args = parser.parse_args()
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if args.command == "returns":
            result = analyze_returns(data, args)
        elif args.command == "trades":
            result = analyze_trades(data)
        else:
            result = analyze_fragility(data, args.center)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except OSError:
        error = "Cannot read input JSON file"
    except (ValueError, OverflowError) as exc:
        error = "Invalid JSON input" if isinstance(exc, json.JSONDecodeError) else str(exc)
    except Exception:
        error = "Tail analysis failed unexpectedly"
    print(json.dumps({"error": error}, ensure_ascii=True))
    return 1


if __name__ == "__main__":
    sys.exit(main())
