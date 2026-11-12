#!/usr/bin/env python3
"""Resolve Chinese stock names / pinyin / fuzzy input to stock codes."""

import argparse
import functools
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _cache import DAY, cached  # noqa: E402
from _subproc import error_message, utf8_stdio  # noqa: E402
from stock_data import _akshare_retry, normalize_us_symbol  # noqa: E402


def _load_stock_map() -> list[dict]:
    def fetch():
        import akshare as ak

        df = _akshare_retry(ak.stock_zh_a_spot_em)
        records = []
        for row in df.to_dict("records"):
            code, name = str(row["代码"]), row["名称"]
            if (
                len(code) != 6
                or not code.isascii()
                or not code.isdigit()
                or not isinstance(name, str)
                or not name.strip()
            ):
                raise ValueError("Stock name map contains invalid codes or names")
            records.append({"code": code, "name": name})
        return records

    try:
        return cached("stock-name-map:v1", DAY, fetch)
    except Exception as e:
        raise RuntimeError(f"Failed to load stock list: {e}") from e


@functools.cache  # one attempt per process: import_parser resolves many names and failures are not disk-cached
def _load_international_stock_map() -> list[dict]:
    # Odd provider rows are skipped: one unexpected code must not drop a whole market's names.
    def fetch_hk():
        import akshare as ak

        records = []
        for row in _akshare_retry(ak.stock_hk_spot_em).to_dict("records"):
            code, name = str(row["代码"]).split(".", 1)[0].zfill(5), row["名称"]
            if code.isdigit() and len(code) == 5 and isinstance(name, str) and name.strip():
                records.append({"code": f"{code}.HK", "name": name})
        return records

    def fetch_us():
        import akshare as ak

        records = []
        for row in _akshare_retry(ak.stock_us_spot_em).to_dict("records"):
            code, name = normalize_us_symbol(row["代码"]), row["名称"]
            if re.fullmatch(r"[A-Z0-9-]{1,10}", code) and isinstance(name, str) and name.strip():
                records.append({"code": code, "name": name})
        return records

    records = []
    for key, fetch in (("stock-name-map:hk:v1", fetch_hk), ("stock-name-map:us:v1", fetch_us)):
        try:
            records.extend(cached(key, DAY, fetch))
        except Exception:
            # One unavailable market must not hide the other markets' names.
            continue
    return records


def _to_pinyin(text: str) -> str:
    try:
        from pypinyin import lazy_pinyin

        return "".join(lazy_pinyin(text)).lower()
    except ImportError:
        return text.lower()


def _to_pinyin_initials(text: str) -> str:
    try:
        from pypinyin import lazy_pinyin

        return "".join(w[0] for w in lazy_pinyin(text) if w).lower()
    except ImportError:
        return ""


def _resolve_stocks(query: str, top: int, stocks: list[dict]) -> list[dict]:
    query = query.strip()
    q_lower = query.lower()
    q_code = query.upper()

    exact = [s for s in stocks if s["code"].upper() == q_code or s["name"] == query]
    if exact:
        return exact[:top]

    prefix_code = [s for s in stocks if s["code"].upper().startswith(q_code)]
    if prefix_code:
        return prefix_code[:top]

    contains = [s for s in stocks if query in s["name"]]
    if contains:
        return contains[:top]

    q_pinyin = _to_pinyin(query)
    q_initials = _to_pinyin_initials(query) if len(query) <= 6 else ""

    scored = []
    for s in stocks:
        name = s["name"]
        name_pinyin = _to_pinyin(name)
        name_initials = _to_pinyin_initials(name)

        best = 0.0
        if q_pinyin and q_pinyin in name_pinyin:
            best = max(best, 0.85)
        if q_initials and q_initials == name_initials:
            best = max(best, 0.9)
        elif q_initials and q_initials in name_initials:
            best = max(best, 0.75)

        ratio = SequenceMatcher(None, q_lower, name.lower()).ratio()
        best = max(best, ratio)

        pinyin_ratio = SequenceMatcher(None, q_pinyin, name_pinyin).ratio()
        best = max(best, pinyin_ratio * 0.95)

        if best > 0.4:
            scored.append((best, s))

    scored.sort(key=lambda x: -x[0])
    return [{"code": s["code"], "name": s["name"], "score": round(sc, 2)} for sc, s in scored[:top]]


def resolve(query: str, top: int = 5) -> list[dict]:
    load_error = None
    try:
        stocks = _load_stock_map()
        local = _resolve_stocks(query, top, stocks)
    except Exception as exc:
        stocks, local, load_error = [], [], exc
    query = query.strip()
    code_like = re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,14}", query)
    # A substring hit (京东 → 京东方A) is not proof the user meant an A share (京东集团-SW is HK).
    partial_name = bool(local) and "score" not in local[0] and query != local[0]["name"] and query in local[0]["name"]
    # Pinyin/initials scores are not proof either (特斯拉 → 天士力 scores 0.9), so only literal hits short-circuit.
    if local and "score" not in local[0] and not code_like and not partial_name:
        return local
    international = _load_international_stock_map()
    if international:
        hits = _resolve_stocks(query, top, stocks + international)
        if load_error is None or (hits and "score" not in hits[0]):  # fuzzy-only hits are a miss
            return hits
    if load_error is not None:
        raise load_error
    return local


def main():
    parser = argparse.ArgumentParser(description="Stock name resolver")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("resolve")
    p.add_argument("query", help="Stock name, pinyin, or partial code")
    p.add_argument("--top", type=int, default=5)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        result = resolve(args.query, args.top)
    except RuntimeError as e:
        result = {"error": error_message(e)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    utf8_stdio()
    main()
