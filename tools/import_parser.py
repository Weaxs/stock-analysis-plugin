#!/usr/bin/env python3
"""Parse stock list from freeform text / CSV / markdown.

Extracts symbols using regex heuristics + name_resolver for A-share Chinese names.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from name_resolver import resolve  # noqa: E402
from stock_data import detect_market  # noqa: E402

# regex patterns
RE_A_CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
RE_HK_CODE = re.compile(r"(?<![A-Za-z0-9])(\d{4,5}\.HK)(?![A-Za-z0-9])", re.IGNORECASE)
RE_ASIA_SUFFIX_CODE = re.compile(
    r"(?<![A-Za-z0-9])(\d{4}\.T|\d{6}\.(?:KS|KQ)|\d{4}\.(?:TW|TWO))(?![A-Za-z0-9])", re.IGNORECASE
)
RE_US_TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,5})(?![A-Za-z0-9\.])")
RE_CN_NAME = re.compile(r"[一-鿿][一-鿿\w]{1,7}")

# US ticker false-positive filter (common ALL-CAPS words that aren't tickers)
US_STOPWORDS = {
    "A",
    "I",
    "AM",
    "PM",
    "AN",
    "AS",
    "AT",
    "BE",
    "BY",
    "DO",
    "GO",
    "IF",
    "IN",
    "IS",
    "IT",
    "ME",
    "MY",
    "NO",
    "OF",
    "ON",
    "OR",
    "SO",
    "TO",
    "UP",
    "US",
    "WE",
    "THE",
    "AND",
    "FOR",
    "BUT",
    "NOT",
    "YOU",
    "ALL",
    "CAN",
    "HAD",
    "HAS",
    "HER",
    "WAS",
    "ONE",
    "OUR",
    "OUT",
    "DAY",
    "GET",
    "HIM",
    "HIS",
    "HOW",
    "MAN",
    "NEW",
    "NOW",
    "OLD",
    "SEE",
    "TWO",
    "WAY",
    "WHO",
    "BOY",
    "DID",
    "ITS",
    "LET",
    "PUT",
    "SAY",
    "SHE",
    "TOO",
    "USE",
    "PE",
    "PB",
    "PS",
    "ROE",
    "MA",
    "RSI",
    "MACD",
    "KDJ",
    "BOLL",
    "ETF",
    "IPO",
    "AI",
    "ML",
    "GDP",
    "CPI",
    "PPI",
    "CEO",
    "CFO",
    "CTO",
    "COO",
    "USA",
    "PDF",
    "CSV",
    "API",
    "URL",
    "HTTP",
    "SQL",
    "JSON",
    "XML",
    "HTML",
    "CSS",
    "OK",
    "TV",
    "PC",
    "OS",
    "USD",
    "CNY",
    "HKD",
    "EUR",
    "JPY",
    "GBP",
}


def _dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for it in items:
        key = it.get("symbol")
        if key and key not in seen:
            seen.add(key)
            result.append(it)
    return result


def parse(text: str) -> dict:
    text = text.strip()
    items: list[dict] = []
    unresolved: list[str] = []
    seen_symbols: set[str] = set()

    # 1. HK codes (do first — they contain digits that A-share regex would also match)
    hk_matches: list[str] = []
    for m in RE_HK_CODE.finditer(text):
        code = m.group(1).upper()
        # normalize to 5-digit
        num, suf = code.split(".")
        code = f"{num.zfill(5)}.HK"
        if code not in seen_symbols:
            items.append({"symbol": code, "market": "HK"})
            seen_symbols.add(code)
            hk_matches.append(m.group(1))

    # 1b. JP/KR/TW suffixed codes (7203.T, 005930.KS, 2330.TW — digits would hit A-share regex too)
    asia_matches: list[str] = []
    for m in RE_ASIA_SUFFIX_CODE.finditer(text):
        code = m.group(1).upper()
        if code not in seen_symbols:
            items.append({"symbol": code, "market": detect_market(code)})
            seen_symbols.add(code)
            asia_matches.append(m.group(1))

    # strip suffixed codes so their digit part doesn't get picked up as A-share
    # and their suffix doesn't get picked up as a US ticker
    text_without_suffixed = text
    for m in hk_matches + asia_matches:
        text_without_suffixed = text_without_suffixed.replace(m, " ")

    # 2. A-share 6-digit codes
    for m in RE_A_CODE.finditer(text_without_suffixed):
        code = m.group(1)
        if code not in seen_symbols:
            items.append({"symbol": code, "market": "A"})
            seen_symbols.add(code)

    # 3. US tickers (1-5 uppercase letters), filtered against stopwords
    # runs on stripped text so HK/JP/KR/TW suffixes (HK, TW, KS...) aren't misread as tickers
    for m in RE_US_TICKER.finditer(text_without_suffixed):
        tick = m.group(1)
        if tick in US_STOPWORDS:
            continue
        if len(tick) == 1:
            continue  # too noisy
        if tick not in seen_symbols:
            items.append({"symbol": tick, "market": "US"})
            seen_symbols.add(tick)

    # 4. Chinese names → resolve via name_resolver
    cn_names = set(RE_CN_NAME.findall(text))
    # filter obviously non-stock words
    noise = {"成本", "持仓", "买入", "卖出", "止损", "止盈", "股票", "代码", "行情", "分析", "策略"}
    cn_names = {n for n in cn_names if n not in noise and len(n) >= 2}

    for name in cn_names:
        try:
            hits = resolve(name, top=1)
        except Exception:
            hits = []
        if hits and isinstance(hits, list) and hits[0].get("code"):
            top_hit = hits[0]
            # ponytail: name_resolver returns nearest fuzzy match even for names not in A-share
            # (e.g. "腾讯" → "拓新药业" via pinyin). Reject low-confidence hits.
            score = top_hit.get("score")
            hit_name = top_hit.get("name", "")
            is_exact = (name in hit_name) or (hit_name in name)
            if not is_exact and score is not None and score < 0.85:
                unresolved.append(name)
                continue
            code = top_hit["code"]
            if code not in seen_symbols:
                items.append(
                    {
                        "symbol": code,
                        "market": "A",
                        "name": hit_name,
                        "resolved_from": name,
                    }
                )
                seen_symbols.add(code)
        else:
            unresolved.append(name)

    return {
        "meta": {
            "provider": "import_parser",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "freshness": "instant",
            "fallback_used": False,
            "warnings": [],
        },
        "items": _dedupe(items),
        "unresolved": unresolved,
        "count": len(items),
    }


def main():
    parser = argparse.ArgumentParser(description="Parse stock list from text")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("parse")
    p.add_argument("--text", help='Text to parse (or "-" for stdin)')
    p.add_argument("--text-b64", help="Base64-encoded text")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.text_b64:
        import base64

        text = base64.b64decode(args.text_b64).decode("utf-8")
    elif not args.text or args.text == "-":
        text = sys.stdin.read()
    else:
        text = args.text
    result = parse(text)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
