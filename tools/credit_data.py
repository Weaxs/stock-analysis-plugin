#!/usr/bin/env python3
"""Market-level CDS index levels: CFETS-SHCH onshore indices and DTCC-disseminated CDX/iTraxx trades."""

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from statistics import median

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cache import WEEK, cached
from _subproc import error_message, scrub_secrets, utf8_stdio
from trading_calendar import market_now

CN_LATEST = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/bond/CDSIndex.json"
CN_HISTORY = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-bond/CDSIndexHis"
CN_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.chinamoney.com.cn/chinese/cdsindx/"}
# The provider rejects pageSize >= 100 with HTTP 403 and windows longer than one calendar month.
PAGE_SIZE = 50
CN_TTL = 4 * 3600
DTCC_LISTING = "https://kgc0418-tdw-data-0.s3.amazonaws.com/dashboard/Cumulative.json"
# CDX HY trades on price; DTCC reports it like spreads (0.010575 = 105.75), so both scale by 10000.
GLOBAL_INDICES = {
    "CDX.NA.IG": "spread_bp",
    "CDX.NA.HY": "price",
    "ITRAXX EUROPE": "spread_bp",
    "ITRAXX EUROPE CROSSOVER": "spread_bp",
}
NOTES = [
    "Market-level credit risk appetite, not single-name CDS or a stock signal.",
    "Rising spreads, or a falling CDX.NA.HY price, mean weaker credit.",
]


def today():
    return market_now("A").date()


def summarize(series):
    values = [value for _, value in series]
    latest = values[-1]
    return {
        "start_date": series[0][0],
        "start": values[0],
        "latest_date": series[-1][0],
        "latest": latest,
        "change": round(latest - values[0], 2),
        "min": min(values),
        "max": max(values),
        "percentile": round(sum(value <= latest for value in values) / len(values) * 100, 1),
        "observations": len(values),
    }


def month_windows(start, end):
    windows = []
    while start <= end:
        # 28 days always fits a calendar month, February included.
        stop = min(start + timedelta(days=27), end)
        windows.append((start, stop))
        start = stop + timedelta(days=1)
    return windows


def number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def pick(candidates, query):
    """Keys whose names equal the query, else contain it (case-insensitive); empty query keeps all."""
    keys = [key for key, _ in candidates]
    query = query.strip()
    if not query:
        return keys
    folded = query.casefold()
    for hit in (lambda name: name.casefold() == folded, lambda name: folded in name.casefold()):
        matched = [key for key, names in candidates if any(name and hit(name) for name in names)]
        if matched:
            return matched
    raise ValueError(f"unknown CDS index {query!r}; available: {', '.join(keys)}")


def cn_rows(name, start, end):
    rows, page = [], 1
    while True:
        params = {
            "indxName": name,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "pageNum": page,
            "pageSize": PAGE_SIZE,
        }
        response = requests.post(CN_HISTORY, params=params, headers=CN_HEADERS, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if (payload.get("data") or {}).get("errorMessage"):
            raise ValueError(f"chinamoney: {payload['data']['errorMessage']}")
        records = payload.get("records") or []
        rows += records
        if not records or len(rows) >= int((payload.get("data") or {}).get("total") or 0):
            return rows
        page += 1


def cn_index(start, end, query, tenor):
    response = requests.post(CN_LATEST, headers=CN_HEADERS, timeout=20)
    response.raise_for_status()
    records = response.json().get("records") or []
    wanted = set(pick([(record["cnName"], [record["cnName"], record.get("enName")]) for record in records], query))
    indices = []
    available = set()
    for record in records:
        name = record["cnName"]
        if name not in wanted:
            continue
        curve = {row["prd"]: number(row["cdIndxVl"]) for row in record.get("dataList") or []}
        curve = {key: value for key, value in curve.items() if value is not None}
        available |= set(curve)
        if tenor and tenor not in curve:
            continue
        series = defaultdict(list)
        for window in month_windows(start, end):
            key = f"cds_cn:{name}:{window[0]}:{window[1]}"
            for row in cached(key, CN_TTL, lambda name=name, window=window: cn_rows(name, *window)):
                if (value := number(row.get("cdIndxVl"))) is not None and (not tenor or row["prd"] == tenor):
                    series[row["prd"]].append((row["date"], value))
        indices.append(
            {
                "name": name,
                "name_en": record.get("enName"),
                "latest_date": record.get("maxDate"),
                "curve": {key: value for key, value in curve.items() if not tenor or key == tenor},
                "history": {key: summarize(sorted(rows)) for key, rows in series.items()},
            }
        )
    if tenor and not indices:
        raise ValueError(f"tenor {tenor} unavailable; available: {', '.join(sorted(available))}")
    if not indices:
        raise ValueError("chinamoney returned no CDS indices")
    return {
        "source": "cn",
        "provider": "chinamoney.com.cn (CFETS-SHCH CDS indices)",
        "unit": "bp",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "indices": indices,
        "notes": [*NOTES, "Provider history covers about the latest year."],
    }


def global_day(rows, day):
    result = {}
    for name in GLOBAL_INDICES:
        trades = [
            row
            for row in rows
            if row.get("UPI Underlier Name") == name
            and row.get("UPI FISN") == "NA/CDS Corp Idx"
            and row.get("Action type") == "NEWT"
            and row.get("Event type") == "TRAD"
            and row.get("Package indicator") == "FALSE"
            and row.get("Spread notation-Leg 1") == "3"
            and str(row.get("Execution Timestamp", "")).startswith(day)
            and number(row.get("Spread-Leg 1")) is not None
        ]
        if not trades:
            continue
        # The most-traded maturity is the on-the-run 5Y series.
        maturity = Counter(row["Expiration Date"] for row in trades).most_common(1)[0][0]
        values = [number(row["Spread-Leg 1"]) * 10000 for row in trades if row["Expiration Date"] == maturity]
        result[name] = {"value": round(median(values), 2), "trades": len(values), "maturity": maturity}
    return result


def dtcc_day(path, day):
    response = requests.get(path, timeout=30)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8-sig")
    return global_day(list(csv.DictReader(io.StringIO(text))), day)


def global_index(days, query):
    names = pick([(name, [name]) for name in GLOBAL_INDICES], query)
    response = requests.get(DTCC_LISTING, timeout=20)
    response.raise_for_status()
    listed = []
    for item in response.json().get("CFTC_CR") or []:
        stamp = item["fileName"].removesuffix(".zip").rsplit("_", 3)[-3:]
        day = date(*map(int, stamp))
        # Weekend files carry no index trades. shortcut: holidays are not skipped, add a calendar if they matter.
        if day.weekday() < 5:
            listed.append((day, item["fullFilePath"]))
    if not listed:
        raise ValueError("DTCC listing has no daily files")
    # A UTC day's file is published after that day closes, so the window ends at the newest weekday file, not today.
    end = max(day for day, _ in listed)
    start = end - timedelta(days=days - 1)
    files = [(day.isoformat(), path) for day, path in listed if start <= day <= end]

    def load(file):
        day, path = file
        try:
            return day, cached(f"cds_dtcc:{day}", WEEK, lambda: dtcc_day(path, day)), None
        except (requests.RequestException, zipfile.BadZipFile, ValueError, KeyError) as exc:
            return day, {}, error_message(exc)

    with ThreadPoolExecutor(max_workers=6) as pool:
        loaded = sorted(pool.map(load, files))
    failed = [{"date": day, "error": error} for day, _, error in loaded if error]
    indices = []
    for name in names:
        quote_type = GLOBAL_INDICES[name]
        observed = [(day, values[name]) for day, values, _ in loaded if name in values]
        if not observed:
            continue
        latest = observed[-1][1]
        # Series rolls change maturity and constituents; compare only the latest series.
        series = [(day, item["value"]) for day, item in observed if item["maturity"] == latest["maturity"]]
        indices.append(
            {
                "name": name,
                "quote_type": quote_type,
                "maturity": latest["maturity"],
                "trades": latest["trades"],
                "summary": summarize(series),
            }
        )
    if not indices:
        raise ValueError("DTCC returned no CDX/iTraxx index trades in the window")
    return {
        "source": "global",
        "provider": "DTCC CFTC public dissemination (median of new on-the-run trades per day)",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "files": len(files),
        "failed_files": failed,
        "indices": indices,
        "notes": [*NOTES, "Daily medians of public trade prints, not official closing levels."],
    }


def cds_index(args):
    if not 1 <= args.days <= 90:
        raise ValueError("days must be 1..90")
    tenor = args.tenor.strip().upper()
    if args.source == "global":
        if tenor not in ("", "5Y"):
            raise ValueError("global CDX/iTraxx levels are on-the-run 5Y only")
        return global_index(args.days, args.index)
    end = today()
    return cn_index(end - timedelta(days=args.days - 1), end, args.index, tenor)


def main():
    parser = argparse.ArgumentParser(description="Market-level CDS index levels")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("cds_index")
    p.add_argument("--source", choices=["cn", "global"], default="cn")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--index", default="", help="Exact or partial index name, e.g. 民企, 科创, CDX.NA.HY")
    p.add_argument("--tenor", default="", help="CN tenor such as 3M or 1Y; global is 5Y only")
    try:
        args = parser.parse_args()
        if args.command != "cds_index":
            raise ValueError("a subcommand is required")
        result = cds_index(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except SystemExit as exc:
        if exc.code:
            print(json.dumps({"error": "invalid CLI arguments"}))
        raise
    except Exception as exc:
        print(json.dumps({"error": scrub_secrets(str(exc))}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    utf8_stdio()
    main()
