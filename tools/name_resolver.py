#!/usr/bin/env python3
"""Resolve Chinese stock names / pinyin / fuzzy input to stock codes."""

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _subproc import error_message, utf8_stdio  # noqa: E402
from stock_data import _EM_HEADERS, normalize_us_symbol  # noqa: E402

_SEARCH_URL = "https://search-codetable.eastmoney.com/codetable/search/web"
_A_TYPES = {"沪A", "深A", "京A", "科创板", "创业板"}


def _search(query: str, count: int) -> list[dict]:
    """Eastmoney code-table search (code / name / pinyin / initials), kept to A/HK/US listed stocks."""
    import requests

    params = {"keyword": query, "pageIndex": 1, "pageSize": count, "client": "web", "clientType": "webSuggest"}
    resp = requests.get(_SEARCH_URL, params={**params, "clientVersion": "lastest"}, headers=_EM_HEADERS, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if str(body.get("code")) != "0":
        raise ValueError(f"stock search unavailable: {str(body.get('msg'))[:100]}")
    stocks = []
    for row in body.get("result") or []:
        code, name, market, kind = (
            str(row.get("code", "")),
            row.get("shortName"),
            row.get("market"),
            row.get("securityTypeName"),
        )
        if not isinstance(name, str) or not name.strip():
            continue
        if market in (0, 1) and kind in _A_TYPES and re.fullmatch(r"\d{6}", code):
            stocks.append({"code": code, "name": name})
        elif market == 116 and kind == "港股" and re.fullmatch(r"\d{5}", code):
            stocks.append({"code": f"{code}.HK", "name": name})
        elif market in (105, 106, 107) and kind == "美股":
            symbol = normalize_us_symbol(code)
            if re.fullmatch(r"[A-Z0-9-]{1,10}", symbol):
                stocks.append({"code": symbol, "name": name})
    return stocks


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
    query = query.strip()
    if not query:
        return []
    try:
        # The provider ranks loosely; over-fetch so the local exact/prefix/contains/pinyin order decides.
        stocks = _search(query, max(top, 20))
    except Exception as e:
        raise RuntimeError(f"Failed to search stock names: {error_message(e)}") from e
    return _resolve_stocks(query, top, stocks)


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
