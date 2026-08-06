#!/usr/bin/env python3
"""Trading calendar — determine trading days for CN/HK/US/JP/KR/TW markets."""

import argparse
import json
import sys
from datetime import datetime, timedelta

# Supported markets. argparse uses type=str.upper so lowercase input is accepted too.
MARKETS = ["CN", "HK", "US", "JP", "KR", "TW"]


def _is_weekend(d: datetime) -> bool:
    return d.weekday() >= 5


def _cn_holidays(year: int) -> set[str]:
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        dates = set()
        for _, row in df.iterrows():
            val = row.iloc[0]
            d = str(val)[:10]
            if d.startswith(str(year)):
                dates.add(d)
        return dates
    except Exception:
        return set()


def _is_exchange_trading_day(exchange_code: str, date_str: str) -> bool:
    import exchange_calendars as xcals
    import pandas as pd

    cal = xcals.get_calendar(exchange_code)
    d = pd.Timestamp(date_str)
    return cal.is_session(d)


def _get_cn_trading_dates(year: int) -> set[str]:
    return _cn_holidays(year)


def is_trading_day(market: str, date_str: str = None) -> dict:
    if date_str:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        d = datetime.now()
        date_str = d.strftime("%Y-%m-%d")

    market = market.upper()

    if _is_weekend(d):
        return {"date": date_str, "market": market, "is_trading_day": False, "reason": "weekend"}

    if market == "CN":
        trading_dates = _get_cn_trading_dates(d.year)
        if trading_dates:
            is_td = date_str in trading_dates
            return {
                "date": date_str,
                "market": market,
                "is_trading_day": is_td,
                "reason": "trading_day" if is_td else "holiday",
            }
        return {
            "date": date_str,
            "market": market,
            "is_trading_day": True,
            "reason": "assumed_trading_day (calendar unavailable)",
        }

    elif market == "HK":
        try:
            is_td = _is_exchange_trading_day("XHKG", date_str)
            return {
                "date": date_str,
                "market": market,
                "is_trading_day": is_td,
                "reason": "trading_day" if is_td else "holiday",
                "source": "exchange-calendars",
            }
        except Exception:
            pass
        trading_dates = _get_cn_trading_dates(d.year)
        if trading_dates:
            is_td = date_str in trading_dates
            return {
                "date": date_str,
                "market": market,
                "is_trading_day": is_td,
                "reason": "trading_day" if is_td else "holiday (approx, based on CN calendar)",
            }
        return {"date": date_str, "market": market, "is_trading_day": True, "reason": "assumed_trading_day"}

    elif market == "US":
        try:
            is_td = _is_exchange_trading_day("XNYS", date_str)
            return {
                "date": date_str,
                "market": market,
                "is_trading_day": is_td,
                "reason": "trading_day" if is_td else "holiday",
                "source": "exchange-calendars",
            }
        except Exception:
            pass
        us_holidays = {
            (1, 1),
            (1, 20),
            (2, 17),
            (5, 26),
            (6, 19),
            (7, 4),
            (9, 1),
            (11, 27),
            (12, 25),
        }
        if (d.month, d.day) in us_holidays:
            return {"date": date_str, "market": market, "is_trading_day": False, "reason": "US_holiday"}
        return {"date": date_str, "market": market, "is_trading_day": True, "reason": "trading_day"}

    elif market in ("JP", "KR", "TW"):
        exchange_map = {"JP": "XTKS", "KR": "XKRX", "TW": "XTAI"}
        try:
            is_td = _is_exchange_trading_day(exchange_map[market], date_str)
            return {
                "date": date_str,
                "market": market,
                "is_trading_day": is_td,
                "reason": "trading_day" if is_td else "holiday",
                "source": "exchange-calendars",
            }
        except Exception:
            pass
        return {
            "date": date_str,
            "market": market,
            "is_trading_day": True,
            "reason": "assumed_trading_day (calendar unavailable)",
        }

    return {"date": date_str, "market": market, "error": f"Unknown market: {market}"}


def next_trading_days(market: str, count: int = 5, from_date: str = None) -> list[str]:
    d = datetime.strptime(from_date, "%Y-%m-%d") if from_date else datetime.now()

    result = []
    max_search = count * 4
    for _ in range(max_search):
        d += timedelta(days=1)
        info = is_trading_day(market, d.strftime("%Y-%m-%d"))
        if info.get("is_trading_day"):
            result.append(d.strftime("%Y-%m-%d"))
            if len(result) >= count:
                break
    return result


def prev_trading_days(market: str, count: int = 5, from_date: str = None) -> list[str]:
    d = datetime.strptime(from_date, "%Y-%m-%d") if from_date else datetime.now()

    result = []
    max_search = count * 4
    for _ in range(max_search):
        d -= timedelta(days=1)
        info = is_trading_day(market, d.strftime("%Y-%m-%d"))
        if info.get("is_trading_day"):
            result.append(d.strftime("%Y-%m-%d"))
            if len(result) >= count:
                break
    return result


def cmd_check(args):
    return is_trading_day(args.market, args.date)


def cmd_next(args):
    days = next_trading_days(args.market, args.count, args.date)
    return {"market": args.market, "next_trading_days": days}


def cmd_prev(args):
    days = prev_trading_days(args.market, args.count, args.date)
    return {"market": args.market, "prev_trading_days": days}


def main():
    parser = argparse.ArgumentParser(description="Trading calendar")
    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser("check")
    p_check.add_argument("market", type=str.upper, choices=MARKETS)
    p_check.add_argument("--date", default=None, help="Date in YYYY-MM-DD format")

    p_next = sub.add_parser("next")
    p_next.add_argument("market", type=str.upper, choices=MARKETS)
    p_next.add_argument("--count", type=int, default=5)
    p_next.add_argument("--date", default=None)

    p_prev = sub.add_parser("prev")
    p_prev.add_argument("market", type=str.upper, choices=MARKETS)
    p_prev.add_argument("--count", type=int, default=5)
    p_prev.add_argument("--date", default=None)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {"check": cmd_check, "next": cmd_next, "prev": cmd_prev}
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
