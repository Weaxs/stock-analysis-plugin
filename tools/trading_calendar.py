#!/usr/bin/env python3
"""Trading calendar — determine trading days for CN/HK/US/JP/KR/TW markets."""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _subproc import socket_timeout, utf8_stdio

# Supported markets. argparse uses type=str.upper so lowercase input is accepted too.
MARKETS = ["CN", "HK", "US", "JP", "KR", "TW"]


def _is_weekend(d: datetime) -> bool:
    return d.weekday() >= 5


# Process-local memo: one CLI call = one process, and the CN path
# (is_trading_day / the prev|next_trading_days walk-back) would otherwise
# re-fetch the sina calendar per step — up to ~5 fetches for one stale check.
_CN_TRADE_DATES: dict[int, set[str]] = {}


def cn_trade_dates(year: int) -> set[str]:
    """CN trading dates (YYYY-MM-DD) for `year` from the sina trade-date history.

    Empty set when the calendar can't be fetched (failures are not cached — the
    next call retries); callers treat empty as "weekends are the only non-trading
    days"."""
    cached = _CN_TRADE_DATES.get(year)
    if cached is not None:
        return cached
    try:
        import akshare as ak

        with socket_timeout(25):
            df = ak.tool_trade_date_hist_sina()
        dates = set()
        for _, row in df.iterrows():
            val = row.iloc[0]
            d = str(val)[:10]
            if d.startswith(str(year)):
                dates.add(d)
    except Exception:
        return set()
    if dates:
        _CN_TRADE_DATES[year] = dates
    return dates


def _is_exchange_trading_day(exchange_code: str, date_str: str) -> bool:
    import exchange_calendars as xcals
    import pandas as pd

    cal = xcals.get_calendar(exchange_code)
    d = pd.Timestamp(date_str)
    return cal.is_session(d)


# --------------- US holiday rules (fallback when exchange-calendars is unavailable) ---------------


def _easter_sunday(year: int) -> datetime:
    """Easter Sunday via the Anonymous Gregorian algorithm (valid for any year ≥ 1583)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return datetime(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    """The n-th `weekday` (Mon=0) of the month, e.g. MLK = _nth_weekday(y, 1, 0, 3)."""
    d = datetime(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> datetime:
    """The last `weekday` of the month, e.g. Memorial Day = _last_weekday(y, 5, 0)."""
    d = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: datetime) -> datetime:
    """NYSE observed-date rule: a Saturday holiday is observed the Friday before,
    a Sunday holiday the Monday after."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _us_holidays(year: int) -> set[tuple[int, int]]:
    """US (XNYS) full-day closure dates for `year` as observed (month, day) pairs —
    rule-computed so every year is correct, not just one hardcoded calendar.

    Fixed-date holidays shift per _observed, except New Year's Day: NYSE stays open
    on Dec 31 when Jan 1 falls on a Saturday (a Sunday New Year is observed Monday).
    Good Friday comes from the Easter algorithm. NYSE closes for Juneteenth from 2022."""
    holidays = set()

    def add(d: datetime):
        if d.year == year:
            holidays.add((d.month, d.day))

    new_year = datetime(year, 1, 1)
    if new_year.weekday() != 5:  # Saturday New Year: no closure, not even Dec 31 prior
        add(_observed(new_year))  # New Year's Day
    add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr. Day — 3rd Monday of January
    add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday — 3rd Monday of February
    add(_easter_sunday(year) - timedelta(days=2))  # Good Friday
    add(_last_weekday(year, 5, 0))  # Memorial Day — last Monday of May
    if year >= 2022:
        add(_observed(datetime(year, 6, 19)))  # Juneteenth (NYSE closes from 2022)
    add(_observed(datetime(year, 7, 4)))  # Independence Day
    add(_nth_weekday(year, 9, 0, 1))  # Labor Day — 1st Monday of September
    add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving — 4th Thursday of November
    add(_observed(datetime(year, 12, 25)))  # Christmas Day
    return holidays


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
        trading_dates = cn_trade_dates(d.year)
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
        trading_dates = cn_trade_dates(d.year)
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
        if (d.month, d.day) in _us_holidays(d.year):
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


# --------------- market phase (trading sessions, market-local time) ---------------

# exchange-calendars codes for the six supported markets. Session times and the
# timezone come from the calendar itself (hard dependency) — no hand-maintained
# tables to drift out of date (e.g. TSE extended its close to 15:30 in 2024-11).
_PHASE_CALENDAR = {"CN": "XSHG", "HK": "XHKG", "US": "XNYS", "JP": "XTKS", "KR": "XKRX", "TW": "XTAI"}


def _market_sessions(market: str):
    """(tz, [("HH:MM", "HH:MM"), ...]) for the market's current regular sessions,
    read off the exchange calendar. Markets without a lunch break get one session."""
    import exchange_calendars as xcals

    cal = xcals.get_calendar(_PHASE_CALENDAR[market])
    open_t, close_t = cal.open_times[-1][1], cal.close_times[-1][1]
    # break_start_times is None for never-broken calendars (XNYS), and its last
    # entry carries None where the break was dropped (XKRX, 2000) — only a real
    # time means lunch
    breaks = cal.break_start_times
    last_break = breaks[-1][1] if breaks else None
    if last_break is not None:
        sessions = [
            (open_t.strftime("%H:%M"), last_break.strftime("%H:%M")),
            (cal.break_end_times[-1][1].strftime("%H:%M"), close_t.strftime("%H:%M")),
        ]
    else:
        sessions = [(open_t.strftime("%H:%M"), close_t.strftime("%H:%M"))]
    return cal.tz, sessions


def market_phase(market: str, at: str = None) -> dict:
    """Trading phase of a market at a market-local time (`at` = "YYYY-MM-DDTHH:MM:SS")."""
    market = market.upper()
    if market not in _PHASE_CALENDAR:
        return {"market": market, "error": f"Unknown market: {market}"}
    try:
        tz, sessions = _market_sessions(market)
    except Exception as e:
        return {"market": market, "error": f"exchange calendar unavailable: {e}"}
    try:
        naive = datetime.strptime(at, "%Y-%m-%dT%H:%M:%S") if at else None
    except ValueError as e:
        return {"market": market, "error": f"invalid --at (want YYYY-MM-DDTHH:MM:SS): {e}"}
    local = datetime.now(tz) if naive is None else naive.replace(tzinfo=tz)
    date_str = local.strftime("%Y-%m-%d")
    is_td = bool(is_trading_day(market, date_str).get("is_trading_day"))
    hm = local.strftime("%H:%M")
    if not is_td:
        phase, label = "closed", "休市"
    elif len(sessions) > 1:
        (m_open, m_close), (a_open, a_close) = sessions
        if hm < m_open:
            phase, label = "pre_market", "盘前"
        elif hm <= m_close:
            phase, label = "morning", "早盘"
        elif hm < a_open:
            phase, label = "lunch_break", "午间休市"
        elif hm <= a_close:
            phase, label = "afternoon", "午盘"
        else:
            phase, label = "post_market", "盘后"
    else:
        s_open, s_close = sessions[0]
        if hm < s_open:
            phase, label = "pre_market", "盘前"
        elif hm <= s_close:
            phase, label = "intraday", "交易中"
        else:
            phase, label = "post_market", "盘后"
    return {
        "market": market,
        "date": date_str,
        "local_time": local.strftime("%Y-%m-%dT%H:%M:%S"),
        "timezone": str(tz),
        "is_trading_day": is_td,
        "phase": phase,
        "phase_label": label,
        "sessions": sessions,
    }


def cmd_next(args):
    days = next_trading_days(args.market, args.count, args.date)
    return {"market": args.market, "next_trading_days": days}


def cmd_prev(args):
    days = prev_trading_days(args.market, args.count, args.date)
    return {"market": args.market, "prev_trading_days": days}


def cmd_phase(args):
    return market_phase(args.market, args.at)


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

    p_phase = sub.add_parser("phase")
    p_phase.add_argument("market", type=str.upper, choices=MARKETS)
    p_phase.add_argument("--at", default=None, help='Market-local time "YYYY-MM-DDTHH:MM:SS" (default: now)')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {"check": cmd_check, "next": cmd_next, "prev": cmd_prev, "phase": cmd_phase}
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    utf8_stdio()
    main()
