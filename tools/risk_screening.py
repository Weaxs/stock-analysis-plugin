#!/usr/bin/env python3
"""Risk screening — 7-dimension risk checker for A-share stocks."""

import argparse
import json
import math
import os
import re
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _subproc import run_tool as _run_tool
from _subproc import utf8_stdio
from stock_data import detect_market
from trading_calendar import market_now


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


# --------------- Check 1: Valuation ---------------


_UNSET = object()


def check_valuation(symbol: str, *, quote=_UNSET) -> dict:
    if quote is _UNSET:
        quote = _run_tool("stock_data.py", ["quote", symbol]) or {}
    if not isinstance(quote, dict) or "error" in quote:
        quote = {}
    pe = _safe_float(quote.get("pe"))
    pb = _safe_float(quote.get("pb"))
    available = pe is not None or pb is not None
    result = {"pe": pe, "pb": pb, "status": "normal" if available else "unknown", "flags": [], "available": available}

    if pe is not None:
        if pe < 0:
            result["flags"].append(
                {"category": "valuation", "severity": "medium", "description": "PE为负，公司亏损", "source": "data"}
            )
            result["status"] = "warning"
        elif pe > 200:
            result["flags"].append(
                {
                    "category": "valuation",
                    "severity": "high",
                    "description": f"PE={pe:.1f}，估值极端偏高",
                    "source": "data",
                }
            )
            result["status"] = "extreme"
        elif pe > 100:
            result["flags"].append(
                {
                    "category": "valuation",
                    "severity": "medium",
                    "description": f"PE={pe:.1f}，估值偏高",
                    "source": "data",
                }
            )
            result["status"] = "warning"

    if pb is not None:
        if pb > 15:
            result["flags"].append(
                {
                    "category": "valuation",
                    "severity": "high",
                    "description": f"PB={pb:.1f}，市净率极端",
                    "source": "data",
                }
            )
            result["status"] = "extreme"
        elif pb > 10:
            result["flags"].append(
                {
                    "category": "valuation",
                    "severity": "medium",
                    "description": f"PB={pb:.1f}，市净率偏高",
                    "source": "data",
                }
            )
            if result["status"] == "normal":
                result["status"] = "warning"

    return result


# --------------- Check 2: Technical Warning ---------------


def check_technical(symbol: str, *, ta=_UNSET) -> dict:
    if ta is _UNSET:
        ta = _run_tool("technical.py", ["analyze", symbol])
    result = {"flags": [], "available": False}

    if not isinstance(ta, dict) or not ta or "error" in ta:
        return result

    signal = ta.get("buy_signal", "")
    score = _safe_float(ta.get("signal_score"))
    result["available"] = score is not None
    trend = (ta.get("trend") or {}).get("overall", "")

    if signal in ("STRONG_SELL",):
        result["flags"].append(
            {
                "category": "technical",
                "severity": "high",
                "description": f"技术信号: {signal}，评分 {score}",
                "source": "data",
            }
        )
    elif signal in ("SELL",):
        result["flags"].append(
            {
                "category": "technical",
                "severity": "medium",
                "description": f"技术信号: {signal}，评分 {score}",
                "source": "data",
            }
        )

    if trend == "bearish" and score is not None and score < 20:
        result["flags"].append(
            {
                "category": "technical",
                "severity": "high",
                "description": "技术面全面走弱（趋势空头+评分<20）",
                "source": "data",
            }
        )

    macd = ta.get("macd", {})
    if macd.get("cross") == "death_cross":
        result["flags"].append(
            {"category": "technical", "severity": "medium", "description": "MACD死叉", "source": "data"}
        )

    result["buy_signal"] = signal
    result["signal_score"] = score
    result["trend"] = trend
    return result


# --------------- Check 3: Lock-up Expiry ---------------


def check_lockup(symbol: str, *, run=None) -> dict:
    result = (run or _run_tool)("risk_screening.py", ["lockup", symbol], timeout=25)
    return (
        result
        if (
            isinstance(result, dict)
            and "error" not in result
            and isinstance(result.get("upcoming_30d"), list)
            and isinstance(result.get("flags"), list)
            and isinstance(result.get("available"), bool)
        )
        else {"upcoming_30d": [], "flags": [], "available": False}
    )


def _fetch_lockup(symbol: str) -> dict:
    result = {"upcoming_30d": [], "flags": [], "available": False}
    try:
        import akshare as ak

        df = ak.stock_restricted_release_queue_em(symbol=symbol)
        if df is None:
            return result
        result["available"] = True
        if df.empty:
            return result

        now = market_now("CN").replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = now + timedelta(days=30)

        date_col = None
        for c in df.columns:
            if "日期" in str(c) or "解禁" in str(c):
                date_col = c
                break
        if date_col is None and len(df.columns) > 0:
            date_col = df.columns[0]

        if date_col:
            import pandas as pd

            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            upcoming = df[(df[date_col] >= now) & (df[date_col] <= cutoff)]
            if not upcoming.empty:
                result["upcoming_30d"] = [str(d.date()) for d in upcoming[date_col].dropna()]
                result["flags"].append(
                    {
                        "category": "lockup",
                        "severity": "medium",
                        "description": f"未来30天有{len(upcoming)}批限售股解禁",
                        "source": "data",
                    }
                )
    except Exception:
        result["available"] = False
    return result


# --------------- Check 4-7: News-based risks ---------------


def _news_risk_queries(search_name: str, market: str) -> dict[str, str]:
    if market == "A":
        return {
            "insider": f"{search_name} 减持 股东减持",
            "earnings": f"{search_name} 业绩预告 亏损 下降",
            "regulatory": f"{search_name} 处罚 违规 监管 立案",
            "industry": f"{search_name} 行业政策 监管 限制",
        }
    if market == "HK":
        return {
            "insider": f"{search_name} insider selling stake reduction 减持",
            "earnings": f"{search_name} profit warning earnings miss 盈警",
            "regulatory": f"{search_name} SFC regulatory investigation penalty 监管",
            "industry": f"{search_name} industry regulation policy headwind",
        }
    return {
        "insider": f"{search_name} insider selling Form 4 sale",
        "earnings": f"{search_name} earnings miss guidance cut loss",
        "regulatory": f"{search_name} SEC investigation lawsuit antitrust fine",
        "industry": f"{search_name} industry regulation tariff headwind",
    }


def check_news_risks(symbol: str, name: str = "", *, run=None, market: str | None = None) -> dict:
    search_name = name if name else symbol
    resolved_market = market or detect_market(symbol)
    categories = _news_risk_queries(search_name, resolved_market)
    result = {"flags": [], "available": True}
    # four independent search_intel subprocesses — fan out concurrently (same
    # ThreadPoolExecutor pattern as gather._run_all); iterating `futures` in
    # declaration order keeps result keys and flags stably ordered
    with ThreadPoolExecutor(max_workers=len(categories)) as pool:
        futures = {
            cat: pool.submit(run or _run_tool, "search_intel.py", ["search", query, "--count", "3"])
            for cat, query in categories.items()
        }
        for cat, future in futures.items():
            data = future.result()
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict) and "error" not in data and isinstance(data.get("results"), list):
                items = data["results"]
            else:
                result["available"] = False

            result[cat] = items
            if items:
                result["flags"].append(
                    {
                        "category": cat,
                        "severity": "medium",
                        "description": f"发现{len(items)}条相关{_cat_cn(cat)}信息",
                        "source": "news",
                    }
                )
    return result


_HK_DISCLOSURE_HIGH_ALWAYS = ("清盘", "winding up", "监管调查", "investigation")
_HK_DISCLOSURE_SUSPENSION = ("停牌", "trading halt", "halt in trading", "suspension")
_HK_RESUMPTION_RE = re.compile(
    r"(resumption\s+of\s+trading|lifting\s+of\s+(?:trading\s+)?suspension|恢复买卖|复牌)",
    re.IGNORECASE,
)
_HK_LIFTING_PHRASE_RE = re.compile(r"lifting\s+of\s+(?:trading\s+)?suspension", re.IGNORECASE)
_HK_DISCLOSURE_MEDIUM = (
    "盈警",
    "盈利警告",
    "profit warning",
    "配售",
    "placing",
    "供股",
    "rights issue",
    "减持",
    "disposal",
)
_HK_SCREEN_TOP = 15


def check_hk_disclosures(symbol: str, *, run=None) -> dict:
    data = (run or _run_tool)(
        "value_data.py", ["announcements", symbol, "--category", "all", "--top", str(_HK_SCREEN_TOP)], timeout=25
    )
    result = {"matches": [], "flags": [], "available": False, "truncated": False, "warnings": []}
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get("announcements"), list):
        return result
    announcements = data["announcements"]
    truncated = bool(data.get("truncated"))
    # Distinguish the intentional 15-row screening cap from an incomplete provider response (< 15 rows while truncated).
    provider_incomplete = truncated and len(announcements) < _HK_SCREEN_TOP
    result["truncated"] = truncated
    result["available"] = not provider_incomplete
    if provider_incomplete:
        result["warnings"].append("HKEX disclosure response was incomplete/truncated; coverage may miss risk filings")
    for item in announcements:
        title = str(item.get("title") or "")
        ann_type = str(item.get("type") or "")
        low = title.lower()
        type_low = ann_type.lower()
        sev = None
        is_resumption_only = (
            bool(_HK_RESUMPTION_RE.search(title))
            and not any(
                k in _HK_LIFTING_PHRASE_RE.sub("", low) for k in ("trading halt", "halt in trading", "suspension")
            )
            and "停牌" not in title.replace("复牌", "")
        )
        has_suspension = any(
            k in low or k in title or k in type_low or k in ann_type for k in _HK_DISCLOSURE_SUSPENSION
        )
        if any(k in low or k in title for k in _HK_DISCLOSURE_HIGH_ALWAYS) or (
            not is_resumption_only and has_suspension
        ):
            sev = "high"
        elif any(k in low or k in title for k in _HK_DISCLOSURE_MEDIUM):
            sev = "medium"
        if sev:
            result["matches"].append({"date": item.get("date"), "title": title, "severity": sev})
    if any(m["severity"] == "high" for m in result["matches"]):
        count = sum(1 for m in result["matches"] if m["severity"] == "high")
        result["flags"].append(
            {
                "category": "disclosure",
                "severity": "high",
                "description": f"港交所近期发现{count}条高风险公告（停牌/清盘/调查类）",
                "source": "data",
            }
        )
    elif result["matches"]:
        result["flags"].append(
            {
                "category": "disclosure",
                "severity": "medium",
                "description": f"港交所近期发现{len(result['matches'])}条风险相关公告（盈警/配售/减持类）",
                "source": "data",
            }
        )
    return result


_F4_ADDITION_RE = re.compile(
    r"(omit(?:ted|ting)?|inadvertently\s+omitted|previously\s+unreported|"
    r"add(?:ed|ing|itional)\s+(?:a\s+)?(?:previously\s+omitted\s+)?transactions?|"
    r"late[- ]?report(?:ed|ing)|补报|漏报|遗漏)",
    re.IGNORECASE,
)
_F4_CORRECTION_RE = re.compile(
    r"(correct(?:ed|ing|ion|s)?|reclassif(?:ied|y|ying)|instead\s+of|"
    r"erroneously\s+reported|incorrectly\s+reported|misreported|should\s+have\s+been|"
    r"replaces?|supersedes?|restat(?:ed|es|ing|ement)|"
    r"(?:revis(?:ed|es|ing)|chang(?:ed|es|ing))\s+from\s+\$?\s*\d|更正|修正|重分类|误报)",
    re.IGNORECASE,
)
_F4_COUNT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_F4_SALE_COUNT = rf"(?:[1-9]\d{{0,3}}|an?|the|{'|'.join(_F4_COUNT_WORDS[1:])})"
_F4_OMITTED = r"(?:(?:previously|inadvertently)\s+)?(?:omitted|unreported)"
_F4_REMARKS_DATE = r"[a-z]+ \d{1,2},? \d{4}"
_F4_DECLARED_NUMBER = r"(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
_F4_PER_SHARE = r"(?:\s+per\s+share)?"
_F4_COUNTED_SALE_ADDITION_RE = re.compile(
    r"(?:(?:this )?(?:form 4(?:/a)?(?: amendment)?|amendment)\s+"
    r"(?:(?:is (?:being )?filed|was filed|filed)\s+to\s+)?|amending to\s+)?"
    r"(?P<verb>add(?:s|ed|ing)?|report(?:s|ed|ing)?|includ(?:e[sd]?|ing))\s+"
    rf"(?P<count>{_F4_SALE_COUNT})\s+"
    rf"(?:(?P<modifier>{_F4_OMITTED}|additional)\s+)?(?:(?P<share_prefix>{_F4_DECLARED_NUMBER})-share\s+)?"
    r"(?P<object>sale(?: transactions?|s)?|transactions?)"
    rf"(?:\s+of (?(share_prefix)|(?:(?P<shares>{_F4_DECLARED_NUMBER})\s+)?)"
    r"(?:(?P<share_class>common|ordinary|class [a-z])\s+)?shares?)?"
    rf"(?:\s+at\s+\$?\s*(?P<price>{_F4_DECLARED_NUMBER}){_F4_PER_SHARE})?"
    rf"(?:\s+(?P<clause>(?:(?:that\s+)?(?:was|were)\s+)?{_F4_OMITTED})"
    rf"(?:\s+from the (?:original )?(?:form 4|filing)(?: filed on (?P<original_date>{_F4_REMARKS_DATE}))?)?)?"
    r"(?:\s+by the reporting (?:person|owner))?",
    re.IGNORECASE | re.ASCII,
)
_F4_ROW_SALE = r"(?:sale(?: transaction)?|transaction(?: line)?|trade)"
_F4_ROW_SALE_SUBJECT = rf"(?:(?:this|the)\s+)?{_F4_ROW_SALE}(?:\s+reported on this line)?"
_F4_ROW_REPORT_VERB = r"(?:report|include|disclose)"
_F4_SALE_QUALIFIER = r"(?:first|second|third|fourth|fifth|private|open-market)"
_F4_ROW_SALE_DESCRIPTION = rf"(?:{_F4_SALE_QUALIFIER}\s+)?{_F4_ROW_SALE}"
_F4_ROW_ADDITION_QUALIFIER_RE = re.compile(rf"\b({_F4_SALE_QUALIFIER})\s+{_F4_ROW_SALE}\b", re.IGNORECASE)
_F4_ROW_ADDITION_FILING_DATE_RE = re.compile(rf"\bfiled on ({_F4_REMARKS_DATE})", re.IGNORECASE)
_F4_ROW_SECURITY = r"(?:\s+of\s+(?:the (?:company|issuer)'s\s+)?(?:common (?:stock|shares)|securities))?"
_F4_ROW_SALE_OBJECT = rf"{_F4_ROW_SALE_DESCRIPTION}{_F4_ROW_SECURITY}"
_F4_ROW_REPORT_CONTEXT = (
    r"(?:\s+from\s+(?:the\s+)?(?:report(?:ing)? person's\s+)?"
    r"(?:(?:original|prior|(?:previously|originally) filed)\s+)?(?:form 4(?:/a)?(?: filing)?|filing|report)"
    rf"(?: (?:originally )?filed on {_F4_REMARKS_DATE})?)?"
    r"(?:\s+that was not previously reported)?(?:\s+on\s+\d{4}-\d{2}-\d{2})?"
)
_F4_ROW_SALE_ADDITION_RE = re.compile(
    rf"(?:{_F4_ROW_SALE_SUBJECT}\s+(?:was|is)\s+(?:{_F4_OMITTED}|newly reported)|"
    r"(?:this (?:amendment|report)\s+|(?:amends (?:the )?original form 4|amending)\s+to\s+|"
    r"(?:this|the) transaction line (?:was|has been) added to (?:this|the) form 4 to\s+)?"
    rf"(?:{_F4_ROW_REPORT_VERB}s?\s+(?:(?:an?|the)\s+)?"
    rf"(?:(?:{_F4_OMITTED}|additional)\s+{_F4_ROW_SALE_OBJECT}|"
    rf"{_F4_ROW_SALE_OBJECT}\s+(?:(?:that|which) was\s+)?{_F4_OMITTED})|"
    rf"adds?\s+(?:(?:an?|the)\s+)?(?:{_F4_OMITTED}\s+)?{_F4_ROW_SALE_OBJECT}(?:\s+{_F4_OMITTED})?)|"
    rf"{_F4_OMITTED}\s+{_F4_ROW_SALE_OBJECT}){_F4_ROW_REPORT_CONTEXT}",
    re.IGNORECASE,
)
_F4_ROW_SALE_DENIAL_CONTEXT = rf"{_F4_ROW_REPORT_CONTEXT}(?:\s+in\s+(?:this|the)\s+(?:amendment|form 4(?:/a)?|report))?"
_F4_CLAUSE_ADVERBS = r"(?:(?:[a-z]+ly|also|still|just|then|instead)\s+)*"
_F4_ROW_SALE_DENIAL_RE = re.compile(
    rf"{_F4_ROW_SALE_SUBJECT}\s+(?:was|is)\s+(?:not|never)\s+(?:{_F4_OMITTED}|newly reported)"
    rf"{_F4_ROW_SALE_DENIAL_CONTEXT}(?=$|(?:\s*,\s*|\s+)(?:and|but)\s+)|"
    rf"(?:this (?:amendment|report)\s+)?(?:does|did)\s+not\s+(?:add|{_F4_ROW_REPORT_VERB})\s+"
    rf"(?:(?:{_F4_SALE_COUNT}|any)\s+)?(?:(?:new|additional|{_F4_OMITTED})\s+)?"
    rf"{_F4_ROW_SALE_DESCRIPTION}s?{_F4_ROW_SECURITY}{_F4_ROW_SALE_DENIAL_CONTEXT}"
    # An unpunctuated conjunction must start a predicate, not a second object such as "purchase prices".
    rf"(?=$|\s*,\s*(?:and|but)\s+|\s+(?:and|but)\s+{_F4_CLAUSE_ADVERBS}"
    rf"(?:(?:it|they|(?:this|the)\s+(?:amendment|report|sale|transaction|filing|row))\s+{_F4_CLAUSE_ADVERBS})?"
    rf"(?:is|are|was|were|does|do|did|has|have|had|will|would|can|could|may|"
    rf"(?:{_F4_ROW_REPORT_VERB}|add|correct|provide|leave|remain)s?)\b)",
    re.IGNORECASE,
)
_F4_SENTENCE_BOUNDARY_RE = re.compile(r"(?:\.(?=\s|$)|[!?;。！？；])+")
_F4_REMARKS_METADATA_RE = re.compile(
    rf"(?:the )?original (?:form 4|filing) was filed on {_F4_REMARKS_DATE}|"
    r"\*\s*(?:[a-z0-9 &,-]+ and )?(?:(?:executive|senior|vice|chief|acting)\s+)*"
    r"(?:president|director|officer|chair|secretary|treasurer)"
    r"(?:,\s*(?-i:[A-Z][A-Za-z0-9&-]*(?:\s+[A-Z][A-Za-z0-9&-]*)*))?",
    re.IGNORECASE | re.ASCII,
)
_F4_OTHER_CHANGE_RE = re.compile(
    r"\b(?:(?:amend|adjust|fix)(?:ed|s|ing)|(?:updat|chang|revis)(?:e[ds]?|ing)|modif(?:ied|ies|ying))\b",
    re.IGNORECASE,
)
_F4_PRIOR_DISCLOSURE_RE = re.compile(
    r"\b(?:(?P<negated>not|never)\s+)?(?:previously|originally|already)\s+(?:reported|disclosed)\b"
    r"(?!\s+(?:form|filing|statement)\b)|"
    r"\b(?:prior|original|existing|old)\s+(?:sale|transaction|trade|price|shares?)\b",
    re.IGNORECASE,
)
_F4_SALE_TO_NONSELL_RE = re.compile(
    r"(\breclassif(?:ied|y|ying)|\btransaction\s+code|\bcode\s+s\s+to\b|"
    r"\b(?:from|misreported|(?:erroneously|incorrectly)\s+reported)\s+(?:as\s+)?(?:a\s+)?(?:[\w-]+\s+){0,3}(?:sale|sell|sold)\b|"
    r"\b(?:sale|sell|sold)\s+(?:should\s+have\s+been|was\s+actually|to\s+(?:a\s+)?(?:bona\s+fide\s+)?(?:gift|non-sale|transfer|disposition))|"
    r"\b(?:instead\s+of|not)\s+(?:a\s+)?(?:[\w-]+\s+){0,2}(?:sale|sell|sold)\b|"
    r"\bto\s+(?:a\s+)?(?:bona\s+fide\s+)?gift\b|"
    r"重分类|改为赠与|误报为卖出|并非卖出)",
    re.IGNORECASE,
)
_F4_SALE_REFERENCE = (
    r"(?:(?:the|a)\s+)?(?:(?:previously (?:misreported|reported(?: in error)?)|original)\s+)?"
    rf"(?:(?P<source_prefix_shares>{_F4_DECLARED_NUMBER})-share\s+)?sale"
    rf"(?:\s+of\s+(?P<source_shares>{_F4_DECLARED_NUMBER})\s+shares?(?:\s+of\s+common\s+stock)?)?"
    rf"(?:\s+at\s+\$?\s*(?P<source_price>{_F4_DECLARED_NUMBER}){_F4_PER_SHARE})?"
)
_F4_CURRENT_CHANGE = (
    r"(?:(?:this amendment\s+)?(?:corrects?|reclassif(?:ies|y)|changes?)|"
    r"this amendment (?:is (?:being )?filed|was filed) to (?:correct|reclassify|change))"
)
_F4_EXPLICIT_SALE_RECLASS_RE = re.compile(
    rf"^(?:(?P<active_change>{_F4_CURRENT_CHANGE})\s+)?{_F4_SALE_REFERENCE}"
    # Without the active verb before the sale, require a passive reclassification verb.
    r"(?(active_change)|\s+(?:was (?:previously reported in error|incorrectly reported) and\s+)?"
    r"(?:(?:is|should be)\s+reclassified|should have been reported))"
    rf"\s+(?:to|as)\s+(?:a\s+)?(?:(?:bona fide|(?P<target_shares>{_F4_DECLARED_NUMBER})-share)\s+)?"
    r"(?P<target>gift|non-sale|transfer|disposition)\b"
    rf"|^{_F4_CURRENT_CHANGE}\s+(?:the\s+)?(?:gift\s+)?(?:transaction\s+)?code\s+(?:from\s+)?"
    r"[\"']?s[\"']?(?:\s*\(sale\))?\s+to\s+[\"']?(?P<target_code>(?!s\b)[a-z])\b",
    re.IGNORECASE,
)
_F4_GIFT_REFERENCE = (
    r"(?:(?:the|this|a)\s+)?(?:(?:previously reported|original)\s+)?"
    r"(?:gift(?:\s+(?:shares|price|quantity))?|number of (?:gift shares|shares gifted)|"
    r"(?:transaction code|price|quantity|(?:number of )?shares)\s+of\s+(?:the|this) gift)\b"
)
_F4_GIFT_CORRECTION_RE = re.compile(
    r"(?:^(?:this amendment\s+)?|\bis\s+(?:being\s+)?(?:filed|amended)\s+(?:solely\s+)?to\s+)"
    r"(?:corrects?|revises?|updates?|restates?|changes?)\s+(?:an error in\s+)?"
    rf"(?:{_F4_GIFT_REFERENCE}|previously reported as\s+[\d,.]+\s+shares?\s+gift\b)|"
    rf"^{_F4_GIFT_REFERENCE}\s+(?:was|were|is|are)\s+(?:corrected|revised|updated|changed)\b",
    re.IGNORECASE,
)
_F4_ROW_GIFT_ADDITION_RE = re.compile(
    rf"(?:(?:this|the) gift (?:was|is) {_F4_OMITTED}|"
    rf"(?:this (?:amendment|report) )?{_F4_ROW_REPORT_VERB}s? (?:a|an|one|the) {_F4_OMITTED} gift)"
    rf"{_F4_ROW_REPORT_CONTEXT}",
    re.IGNORECASE,
)
_F4_ROW_GIFT_ADDITION_DENIAL_RE = re.compile(
    rf"(?:this|the) gift (?:was|is) (?:not|never) {_F4_OMITTED}\b|"
    rf"(?:this (?:amendment|report) )?(?:does|did) not (?:add|{_F4_ROW_REPORT_VERB}) "
    rf"(?:(?:a|an|any|one|the) )?(?:(?:new|additional|{_F4_OMITTED}) )?gifts?"
    rf"{_F4_ROW_SALE_DENIAL_CONTEXT}(?=$|(?:\s*,\s*|\s+)(?:and|but)\s+)",
    re.IGNORECASE,
)
_F4_RESTORE_SELL_RE = re.compile(
    r"(\breclassif(?:ied|y|ying)|\btransaction\s+code|\bcode\s+s\b|"
    r"\b(?:was|is|should\s+have\s+been)\s+(?:actually\s+)?(?:a\s+)?(?:[\w-]+\s+){0,2}sale\b|"
    r"\b(?:instead\s+of|not)\s+(?:a\s+)?(?:[\w-]+\s+){0,2}(?:gift|non-sale|transfer)\b|"
    r"\b(?:from|misreported|(?:erroneously|incorrectly)\s+reported)\s+(?:as\s+)?(?:a\s+)?(?:[\w-]+\s+){0,3}(?:gift|non-sale)\b|"
    r"\bpreviously\s+reported\s+as\s+(?:a\s+)?(?:[\w-]+\s+){0,3}(?:gift|non-sale)\b|"
    r"重分类|改为卖出|恢复为卖出|并非赠与)",
    re.IGNORECASE,
)
_F4_DATE_CORR_RE = re.compile(
    r"(transaction\s+date|trade\s+date|date\s+of\s+(?:the\s+)?(?:transaction|sale)|"
    r"from\s+\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2}|交易日期|成交日期)",
    re.IGNORECASE,
)
_F4_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_F4_PRIOR_PRICE_EXPLICIT_RE = re.compile(
    r"(?:from|previously(?:\s+reported)?(?:\s+at)?|was|instead\s+of|originally(?:\s+reported)?(?:\s+at)?)"
    r"\s+(?:a\s+price\s+of\s+)?\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\b|"
    r"\b(?:price|px)\s+(?:from|was|of)\s+\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_F4_PRIOR_PRICE_LINKED_RE = re.compile(
    r"\b\d+(?:,\d{3})*(?:\.\d+)?\s+shares?\s+at\s+\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\b|"
    r"\b(?:corrects?|correcting|corrected|moves?|moving|moved|amends?|amending|amended)\s+"
    r"(?:the\s+|a\s+)?(?:[\w-]+\s+){0,3}?(?:sale|transaction|line|row)\s+at\s+\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_F4_PRIOR_SHARES_RE = re.compile(
    r"(?:from|previously(?:\s+reported)?(?:\s+as)?|was|instead\s+of|originally(?:\s+reported)?(?:\s+as)?)"
    r"\s+(\d+(?:,\d{3})*(?:\.\d+)?)\s+shares?\b|"
    r"\b(\d+(?:,\d{3})*(?:\.\d+)?)\s+shares?\s+(?:at\s+\$|on\s+\d{4}-\d{2}-\d{2})|"
    r"(?:misreported|reported|prior|original)\s+(\d+(?:,\d{3})*(?:\.\d+)?)[- ]shares?\s+(?:sale|transaction)\b",
    re.IGNORECASE,
)


def _f4_tx_code(tx: dict) -> str:
    return str(tx.get("code") or tx.get("transaction_code") or "").upper()


def _f4_is_sell(tx: dict) -> bool:
    return _f4_tx_code(tx) == "S" or str(tx.get("action") or "").lower() == "sell"


def _f4_owner_key(tx: dict) -> tuple:
    owners = tx.get("owners")
    if isinstance(owners, list) and owners:
        return tuple(sorted(str(o.get("cik") or o.get("name") or "") for o in owners if isinstance(o, dict)))
    return (str(tx.get("owner") or tx.get("insider") or ""),)


def _f4_base_sig(tx: dict) -> tuple:
    return (
        _f4_owner_key(tx),
        str(tx.get("transaction_date") or tx.get("date") or ""),
        str(tx.get("security") or ""),
        bool(tx.get("derivative")),
    )


def _f4_exact_sig(tx: dict) -> tuple:
    return (
        _f4_owner_key(tx),
        str(tx.get("transaction_date") or tx.get("date") or ""),
        str(tx.get("security") or ""),
        bool(tx.get("derivative")),
        tx.get("shares"),
        tx.get("price"),
    )


def _f4_owner_sec_sig(tx: dict) -> tuple:
    return (_f4_owner_key(tx), str(tx.get("security") or ""), bool(tx.get("derivative")))


def _f4_rev_tuple(tx: dict) -> tuple[str, object, object]:
    td = str(tx.get("transaction_date") or tx.get("date") or "").strip()[:10]
    return (td, tx.get("shares"), tx.get("price"))


def _f4_tx_date_iso(tx: dict) -> str | None:
    raw = str(tx.get("transaction_date") or tx.get("date") or "").strip()[:10]
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _f4_normalize_acceptance_ts(raw) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        if re.fullmatch(r"\d{14}", s):
            dt = datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        else:
            if s.endswith(("Z", "z")):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            dt = dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return dt.isoformat(timespec="microseconds")


def _f4_matches_num_set(val, target_set: set[float]) -> bool:
    vf = _safe_float(val)
    return vf is not None and any(math.isclose(vf, t, rel_tol=0.0, abs_tol=1e-6) for t in target_set)


def _f4_correction_sentences(rtext: str) -> str:
    # Split on sentence boundaries (including periods after trailing numbers like "$30.") while keeping decimals intact.
    sents = [s for s in _F4_SENTENCE_BOUNDARY_RE.split(rtext) if s.strip()]
    corr_sents = [s for s in sents if _F4_CORRECTION_RE.search(s)]
    return ". ".join(corr_sents) if corr_sents else rtext


def _f4_stated_sale_conversion_values(rtext: str, *fields: str) -> set[float]:
    values: set[float] = set()
    for sentence in _f4_statements(rtext):
        match = _F4_EXPLICIT_SALE_RECLASS_RE.match(sentence)
        if match:
            for field in fields:
                value = _safe_float((match[field] or "").replace(",", ""))
                if value is not None:
                    values.add(value)
    return values


def _f4_stated_prior_prices(rtext: str, new_price) -> set[float]:
    if conversion_values := _f4_stated_sale_conversion_values(rtext, "source_price"):
        return conversion_values
    new_f = _safe_float(new_price)
    scoped = _f4_correction_sentences(rtext)
    explicit: set[float] = set()
    for m in _F4_PRIOR_PRICE_EXPLICIT_RE.finditer(scoped):
        tok = next((g for g in m.groups() if g), "").replace(",", "")
        val = _safe_float(tok)
        if val is not None and (new_f is None or not math.isclose(val, new_f, rel_tol=0.0, abs_tol=1e-6)):
            explicit.add(val)
    if explicit:
        return explicit
    linked: set[float] = set()
    for m in _F4_PRIOR_PRICE_LINKED_RE.finditer(scoped):
        tok = next((g for g in m.groups() if g), "").replace(",", "")
        val = _safe_float(tok)
        if val is not None and (new_f is None or not math.isclose(val, new_f, rel_tol=0.0, abs_tol=1e-6)):
            linked.add(val)
    return linked


def _f4_stated_prior_shares(rtext: str, new_shares) -> set[float]:
    if conversion_values := _f4_stated_sale_conversion_values(rtext, "source_shares", "source_prefix_shares"):
        return conversion_values
    out: set[float] = set()
    new_f = _safe_float(new_shares)
    for m in _F4_PRIOR_SHARES_RE.finditer(_f4_correction_sentences(rtext)):
        tok = next((g for g in m.groups() if g), "").replace(",", "")
        val = _safe_float(tok)
        if val is not None and (new_f is None or not math.isclose(val, new_f, rel_tol=0.0, abs_tol=1e-6)):
            out.add(val)
    return out


def _f4_has_date_corr_evidence(tx: dict, rtext: str) -> bool:
    if _F4_DATE_CORR_RE.search(rtext):
        return True
    refs = tx.get("footnote_refs")
    return isinstance(refs, dict) and any("transactiondate" in str(k).lower() for k in refs)


def _f4_has_amount_corr_evidence(tx: dict, rtext: str) -> bool:
    if re.search(r"\b(?:price|px|shares?)\b|价格|股数", rtext, re.IGNORECASE):
        return True
    refs = tx.get("footnote_refs")
    return isinstance(refs, dict) and any(
        "transactionprice" in str(k).lower() or "transactionshares" in str(k).lower() for k in refs
    )


def _f4_extract_ref_ids(refs) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    if isinstance(refs, dict):
        for ids in refs.values():
            if isinstance(ids, list):
                for fid in ids:
                    if fid and str(fid) not in seen:
                        seen.add(str(fid))
                        out.append(str(fid))
            elif isinstance(ids, str) and ids not in seen:
                seen.add(ids)
                out.append(ids)
    elif isinstance(refs, list):
        for fid in refs:
            if fid and str(fid) not in seen:
                seen.add(str(fid))
                out.append(str(fid))
    return out


def _f4_statements(text: str) -> list[str]:
    return [part.strip() for part in _F4_SENTENCE_BOUNDARY_RE.split(text) if part.strip()]


class _F4AdditionDeclaration(NamedTuple):
    count: int
    sales_only: bool
    shares: float | None
    price: float | None
    share_class: str | None
    original_date: str | None

    def matches(self, tx: dict) -> bool:
        class_end = r"\b(?!-\w)" if (self.share_class or "").startswith("class ") else r"\b"
        security = str(tx.get("security") or "")
        return (
            (not self.sales_only or _f4_is_sell(tx))
            and (self.shares is None or _f4_matches_num_set(tx.get("shares"), {self.shares}))
            and (self.price is None or _f4_matches_num_set(tx.get("price"), {self.price}))
            and (
                self.share_class is None
                or re.search(rf"\b{self.share_class}{class_end}", security, re.IGNORECASE) is not None
            )
            and (self.original_date is None or tx.get("original_filing_date") == self.original_date)
        )

    def merged_with(self, other: "_F4AdditionDeclaration") -> "_F4AdditionDeclaration | None":
        if self[:2] != other[:2] or any(
            a is not None and b is not None and a != b for a, b in zip(self[2:], other[2:])
        ):
            return None
        return _F4AdditionDeclaration(
            self.count,
            self.sales_only,
            self.shares if self.shares is not None else other.shares,
            self.price if self.price is not None else other.price,
            self.share_class or other.share_class,
            self.original_date or other.original_date,
        )


def _f4_declared_filing_date(value: str | None) -> str | None:
    if value:
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(value.replace(",", ""), fmt).date().isoformat()
            except ValueError:
                continue
    return value


def _f4_sale_addition_declaration(text: str) -> _F4AdditionDeclaration | None:
    match = _F4_COUNTED_SALE_ADDITION_RE.fullmatch(text)
    if not match or not (match["modifier"] or match["clause"] or match["verb"].lower().startswith("add")):
        return None
    count = match["count"].lower()
    subject = match["object"].lower()
    if count.isdigit():
        declared = int(count)
    elif count in {"a", "an", "the"}:
        declared = int(not subject.endswith("s"))
    else:
        declared = _F4_COUNT_WORDS.index(count)
    shares = _safe_float((match["shares"] or match["share_prefix"] or "").replace(",", ""))
    price = _safe_float((match["price"] or "").replace(",", ""))
    return _F4AdditionDeclaration(
        declared,
        subject.startswith("sale"),
        shares,
        price,
        match["share_class"].casefold() if match["share_class"] else None,
        _f4_declared_filing_date(match["original_date"]),
    )


def _f4_uncounted_row_addition(text: str) -> tuple[_F4AdditionDeclaration, str | None] | None:
    if not _F4_ROW_SALE_ADDITION_RE.fullmatch(text):
        return None
    date_match = _F4_ROW_ADDITION_FILING_DATE_RE.search(text)
    original_date = _f4_declared_filing_date(date_match[1]) if date_match else None
    qualifier = _F4_ROW_ADDITION_QUALIFIER_RE.search(text)
    return _F4AdditionDeclaration(1, True, None, None, None, original_date), qualifier[
        1
    ].casefold() if qualifier else None


class _F4AdditionClaim(NamedTuple):
    declaration: _F4AdditionDeclaration
    refs: set[int]
    counted: bool = True
    qualifier: str | None = None

    def matching_rows(self, rows: list[dict], eligible: set[int], qualifiers_by_row: list[set[str]]) -> set[int]:
        return {
            i
            for i in self.refs & eligible
            if self.declaration.matches(rows[i]) and (self.qualifier is None or self.qualifier in qualifiers_by_row[i])
        }


def _f4_merge_addition_claims(
    shared: list[_F4AdditionClaim],
    remarks: list[_F4AdditionClaim],
    rows: list[dict],
    additions: set[int],
    qualifiers_by_row: list[set[str]],
) -> list[_F4AdditionClaim]:
    claims: list[_F4AdditionClaim] = []
    unpaired: list[_F4AdditionClaim] = []
    remaining_remarks = list(remarks)
    all_rows = set(range(len(rows)))
    for claim in shared:
        copy_in_remarks = claim._replace(refs=all_rows)
        if copy_in_remarks in remaining_remarks:
            remaining_remarks.remove(copy_in_remarks)
            claims.append(claim)
        else:
            unpaired.append(claim)
    # A restated row cannot corroborate a merge. A count that covers the whole accession can establish additions alone.
    options: list[dict[int, _F4AdditionClaim]] = []
    for remark in remaining_remarks:
        candidates: dict[int, _F4AdditionClaim] = {}
        for i, claim in enumerate(unpaired):
            if not (remark.counted or claim.counted):
                continue
            declaration = claim.declaration.merged_with(remark.declaration)
            if declaration is None or not declaration.count:
                continue
            merged = _F4AdditionClaim(
                declaration, claim.refs, claim.counted and remark.counted, claim.qualifier or remark.qualifier
            )
            if merged.counted:
                eligible = all_rows if declaration.count == len(rows) and claim.refs == all_rows else additions
                if len(merged.matching_rows(rows, eligible, qualifiers_by_row)) >= declaration.count:
                    candidates[i] = merged
            else:
                # The counted statement and row description must independently identify the same addition.
                linked = claim.matching_rows(rows, additions, qualifiers_by_row)
                if len(linked) == 1 and linked == remark.matching_rows(rows, additions, qualifiers_by_row):
                    candidates[i] = merged
        options.append(candidates)
    paired: set[int] = set()
    for remark, candidates in zip(remaining_remarks, options):
        if len(candidates) == 1:
            i, merged = next(iter(candidates.items()))
            if sum(i in others for others in options) == 1:
                claims.append(merged)
                paired.add(i)
                continue
        claims.append(remark)
    return claims + [claim for i, claim in enumerate(unpaired) if i not in paired]


def _f4_filing_additions_covered(
    claims: list[_F4AdditionClaim], rows: list[dict], additions: set[int], qualifiers_by_row: list[set[str]]
) -> bool:
    if not claims:
        return bool(additions)
    options: list[set[int]] = []
    for claim in claims:
        count = claim.declaration.count
        eligible = claim.matching_rows(rows, additions, qualifiers_by_row)
        if not count or len(eligible) < count or len(options) + count > len(additions):
            return False
        options.extend([eligible] * count)
    # Assign every declared transaction to a distinct row, reassigning earlier claims when needed.
    options.sort(key=len)
    assigned: dict[int, int] = {}
    for claim in range(len(options)):
        previous: dict[int, tuple[int, int] | None] = {claim: None}
        pending = [claim]
        for current in pending:
            free = options[current] - assigned.keys()
            if free:
                assigned[free.pop()] = current
                while (step := previous[current]) is not None:
                    current, row = step
                    assigned[row] = current
                break
            for row in options[current]:
                owner = assigned[row]
                if owner not in previous:
                    previous[owner] = current, row
                    pending.append(owner)
        else:
            return False
    return True


def _f4_conversion_targets(
    acc_rows: list[dict],
    refs: set[int],
    conversion: re.Match[str],
    ledger: list[dict],
    non_sell_histories: list[list[dict]],
    local_gift_corrections: dict[int, list[str]],
    local_gift_additions: set[int],
) -> list[int]:
    code = conversion["target_code"] or ("G" if (conversion["target"] or "").casefold() == "gift" else None)
    quantity = _safe_float((conversion["target_shares"] or "").replace(",", ""))
    candidates = [
        i
        for i in refs
        if not _f4_is_sell(acc_rows[i])
        and (code is None or _f4_tx_code(acc_rows[i]) == code.upper())
        and (quantity is None or _f4_matches_num_set(acc_rows[i].get("shares"), {quantity}))
    ]
    if len(candidates) <= 1:
        return candidates if len(refs) == 1 else [i for i in candidates if i not in local_gift_additions]

    # Multiple linked gifts can only be distinguished by the corrected transaction and its filing history.
    sentence = conversion.string
    prior_prices = _f4_stated_prior_prices(sentence, None)
    prior_shares = _f4_stated_prior_shares(sentence, None)
    matched = []
    for i in candidates:
        tx = acc_rows[i]
        pool, unlinked = _f4_scoped_pool(ledger, tx, len(ledger))
        if not unlinked and any(
            _f4_base_sig(ledger[j]["tx"]) == _f4_base_sig(tx)
            and _f4_entry_has_revision(
                ledger[j],
                shares_target=prior_shares or None,
                price_target=prior_prices or None,
                exact_shares=tx.get("shares") if not prior_shares and quantity is None else _UNSET,
            )
            for j in pool
        ):
            matched.append(i)
    if not matched:
        return candidates
    # Reject separately reported gifts without promoting an unmatched row by elimination.
    matched = [i for i in matched if i not in local_gift_additions]
    if len(matched) <= 1:
        return matched

    # A row-specific correction with its own prior native gift accounts for a separate transaction.
    independent_gifts = set()
    for i in matched:
        tx = acc_rows[i]
        if _f4_tx_code(tx) != "G" or i not in local_gift_corrections:
            continue
        correction = ". ".join(local_gift_corrections[i])
        gift_shares = _f4_stated_prior_shares(correction, tx.get("shares"))
        gift_prices = _f4_stated_prior_prices(correction, tx.get("price"))
        if not (gift_shares or gift_prices):
            continue
        linked_date = str(tx.get("original_filing_date") or "").strip()
        native = _f4_prior_native_gifts(
            non_sell_histories, tx, linked_date, shares_target=gift_shares, price_target=gift_prices
        )
        if len(native) == 1:
            pool, _ = _f4_scoped_pool(ledger, tx, len(ledger))
            if not _f4_prior_reclassified_gifts(ledger, tx, pool, gift_shares, gift_prices):
                independent_gifts.add(i)
    return [i for i in matched if i not in independent_gifts] or matched


def _f4_accession_note_texts(
    acc_rows: list[dict], ledger: list[dict], non_sell_histories: list[list[dict]], remark_conversions: list[str]
) -> tuple[list[str], list[_F4AdditionClaim], set[int]]:
    note_rows: dict[tuple[str | None, str], tuple[str, set[int]]] = {}
    texts_by_row: list[list[str]] = [[] for _ in acc_rows]
    for i, tx in enumerate(acc_rows):
        footnotes = tx.get("footnotes")
        if isinstance(footnotes, dict):
            for ref_id in _f4_extract_ref_ids(tx.get("footnote_refs")):
                for sentence in _f4_statements(str(footnotes.get(ref_id) or "")):
                    key = (ref_id, sentence.casefold())
                    note_rows.setdefault(key, (sentence, set()))[1].add(i)
        elif isinstance(footnotes, str) and footnotes:
            texts_by_row[i].append(footnotes)

    # An identical filing remark must not widen the references supplied by a linked footnote.
    linked_statements = {statement for _, statement in note_rows}
    all_rows = set(range(len(acc_rows)))
    for sentence in remark_conversions:
        statement = sentence.casefold()
        if statement not in linked_statements:
            note_rows[None, statement] = sentence, all_rows

    local_gift_notes: dict[int, list[str]] = {}
    for sentence, refs in note_rows.values():
        if len(refs) == 1:
            i = next(iter(refs))
            if _f4_tx_code(acc_rows[i]) == "G":
                local_gift_notes.setdefault(i, []).append(sentence)
    local_gift_corrections: dict[int, list[str]] = {}
    local_gift_additions: set[int] = set()
    for i, sentences in local_gift_notes.items():
        corrections = [sentence for sentence in sentences if _F4_GIFT_CORRECTION_RE.search(sentence)]
        if corrections:
            local_gift_corrections[i] = corrections
        elif any(_F4_ROW_GIFT_ADDITION_RE.fullmatch(sentence) for sentence in sentences) and not any(
            _F4_ROW_GIFT_ADDITION_DENIAL_RE.match(sentence)
            or _F4_CORRECTION_RE.search(sentence)
            or _F4_EXPLICIT_SALE_RECLASS_RE.match(sentence)
            or any(not match["negated"] for match in _F4_PRIOR_DISCLOSURE_RE.finditer(sentence))
            for sentence in sentences
        ):
            local_gift_additions.add(i)

    shared_additions: list[_F4AdditionClaim] = []
    unresolved_conversions: set[int] = set()
    for sentence, refs in note_rows.values():
        if conversion := _F4_EXPLICIT_SALE_RECLASS_RE.match(sentence):
            targets = _f4_conversion_targets(
                acc_rows, refs, conversion, ledger, non_sell_histories, local_gift_corrections, local_gift_additions
            )
            if len(targets) == 1:
                texts_by_row[targets[0]].append(sentence)
            else:
                unresolved_conversions.update(targets or refs)
            continue
        if len(refs) > 1 and any(_f4_is_sell(acc_rows[i]) for i in refs):
            declaration = _f4_sale_addition_declaration(sentence)
            if declaration is not None:
                shared_additions.append(_F4AdditionClaim(declaration, refs))
                continue
            if row_addition := _f4_uncounted_row_addition(sentence):
                declaration, qualifier = row_addition
                shared_additions.append(_F4AdditionClaim(declaration, refs, counted=False, qualifier=qualifier))
                continue
        for i in refs:
            texts_by_row[i].append(sentence)
    for row_parts, tx in zip(texts_by_row, acc_rows):
        row_parts.extend(str(tx[key]) for key in ("amendment_reason", "note") if tx.get(key))
    row_texts = [
        ". ".join(part.strip().rstrip(".") for part in row_parts if part.strip()) for row_parts in texts_by_row
    ]
    return row_texts, shared_additions, unresolved_conversions


def _f4_resolve_accession_intents(
    acc_rows: list[dict],
    ledger: list[dict],
    non_sell_histories: list[list[dict]],
) -> tuple[list[tuple[bool, bool]], list[bool], list[str], set[int]]:
    """Resolve per-row (is_add, is_corr) strictly from row-scoped evidence.

    Use filing evidence for a single unambiguous row or an explicit sale addition count
    covering every row. Leave unattributed rows in mixed filings uncertain.
    """
    remarks_parts = list(dict.fromkeys(rem for tx in acc_rows if (rem := str(tx.get("remarks") or "").strip())))
    remarks_sentences = [sentence for text in remarks_parts for sentence in _f4_statements(text)]
    remark_conversions = []
    other_remarks = []
    for sentence in remarks_sentences:
        if _F4_EXPLICIT_SALE_RECLASS_RE.match(sentence):
            remark_conversions.append(sentence)
        else:
            other_remarks.append(sentence)
    row_texts, shared_additions, unresolved_conversions = _f4_accession_note_texts(
        acc_rows, ledger, non_sell_histories, remark_conversions
    )
    prior_disclosures = [
        any(not match["negated"] for match in _F4_PRIOR_DISCLOSURE_RE.finditer(text)) for text in row_texts
    ]
    row_intents = []
    ambiguous = [i in unresolved_conversions for i in range(len(acc_rows))]
    qualifiers_by_row: list[set[str]] = [set() for _ in acc_rows]
    row_denies_addition = False
    for i, (tx, rt, prior) in enumerate(zip(acc_rows, row_texts, prior_disclosures)):
        if _f4_is_sell(tx):
            sentences = _f4_statements(rt)
            denial = any(_F4_ROW_SALE_DENIAL_RE.match(sentence) for sentence in sentences)
            row_denies_addition |= denial
            singular_declarations = []
            for sentence in sentences:
                declaration = _f4_sale_addition_declaration(sentence)
                if declaration is not None and declaration.count == 1:
                    singular_declarations.append(declaration)
                elif row_addition := _f4_uncounted_row_addition(sentence):
                    declaration, qualifier = row_addition
                    singular_declarations.append(declaration)
                    if qualifier is not None:
                        qualifiers_by_row[i].add(qualifier)
            contradictory = any(not declaration.matches(tx) for declaration in singular_declarations)
            if contradictory:
                ambiguous[i] = True
            addition = not (prior or denial or contradictory) and bool(singular_declarations)
        else:
            addition = bool(_F4_ADDITION_RE.search(rt))
        row_intents.append((addition, bool(_F4_CORRECTION_RE.search(rt))))
    if not (other_remarks or shared_additions):
        return row_intents, ambiguous, row_texts, unresolved_conversions
    rem_text = ". ".join(other_remarks)
    # shortcut: promote explicit English declarations, and expand for verified filing wording.
    all_rows = set(range(len(acc_rows)))
    remarks_claims = []
    mixed_filing_evidence = False
    for sentence in other_remarks:
        declaration = _f4_sale_addition_declaration(sentence)
        if declaration is not None:
            remarks_claims.append(_F4AdditionClaim(declaration, all_rows))
        elif row_addition := _f4_uncounted_row_addition(sentence):
            declaration, qualifier = row_addition
            remarks_claims.append(_F4AdditionClaim(declaration, all_rows, counted=False, qualifier=qualifier))
        elif not _F4_REMARKS_METADATA_RE.fullmatch(sentence) or _F4_OTHER_CHANGE_RE.search(sentence):
            mixed_filing_evidence = True
    claimed_additions = {i for i, (addition, correction) in enumerate(row_intents) if addition and not correction}
    claims = _f4_merge_addition_claims(shared_additions, remarks_claims, acc_rows, claimed_additions, qualifiers_by_row)
    covering_declaration = (
        claims[0].declaration if len(claims) == 1 and claims[0].counted and claims[0].refs == all_rows else None
    )
    filing_add = bool(claims) or bool(_F4_ADDITION_RE.search(rem_text))
    rem_corr = bool(_F4_CORRECTION_RE.search(rem_text))
    if not (filing_add or rem_corr):
        return row_intents, ambiguous, row_texts, unresolved_conversions
    if (
        len(remarks_parts) <= 1
        and covering_declaration is not None
        and covering_declaration.count == len(acc_rows)
        and (len(acc_rows) == 1 or covering_declaration.sales_only)
        and all(covering_declaration.matches(tx) for tx in acc_rows)
        and not mixed_filing_evidence
        and not any(ambiguous)
        and not rem_corr
        and not row_denies_addition
        and not any(prior_disclosures)
        and not any(correction for _, correction in row_intents)
        and not any(_F4_OTHER_CHANGE_RE.search(text) for text in row_texts)
        and not any(
            not addition and _F4_ADDITION_RE.search(text) for text, (addition, _) in zip(row_texts, row_intents)
        )
        and all(_f4_is_sell(tx) for tx in acc_rows)
    ):
        for tx, (addition, _) in zip(acc_rows, row_intents):
            if addition:
                continue
            pool, _ = _f4_scoped_pool(ledger, tx, len(ledger))
            sig = _f4_exact_sig(tx)
            if any(sig in ledger[i]["sigs"] for i in pool):
                break
        else:
            return [(True, False)] * len(acc_rows), ambiguous, row_texts, unresolved_conversions
    unclaimed_add = filing_add and not _f4_filing_additions_covered(
        claims, acc_rows, claimed_additions, qualifiers_by_row
    )
    unclaimed_corr = rem_corr and not any(rc for _, rc in row_intents)
    if not (unclaimed_add or unclaimed_corr):
        return row_intents, ambiguous, row_texts, unresolved_conversions
    unattributed = [i for i, (ra, rc) in enumerate(row_intents) if not (ra or rc)]
    if len(acc_rows) == 1 and len(unattributed) == 1 and (unclaimed_add != unclaimed_corr):
        if unclaimed_add and _f4_is_sell(acc_rows[0]):
            ambiguous[0] = True
            return row_intents, ambiguous, row_texts, unresolved_conversions
        row_intents[0] = (unclaimed_add, unclaimed_corr)
        row_texts[0] = ". ".join(filter(None, (row_texts[0], rem_text)))
        return row_intents, ambiguous, row_texts, unresolved_conversions
    for i in unattributed or range(len(acc_rows)):
        ambiguous[i] = True
    return row_intents, ambiguous, row_texts, unresolved_conversions


def _f4_make_ledger_entry(tx: dict, *, from_orig: bool) -> dict:
    shares = tx.get("shares")
    fdate = str(tx.get("filing_date") or "").strip()
    orig_fdate = str(tx.get("original_filing_date") or "").strip() if not from_orig else ""
    filing_dates = {d for d in (fdate, orig_fdate) if d}
    return {
        "tx": tx,
        "orig_tx": tx,
        "sigs": {_f4_exact_sig(tx)},
        "revisions": [_f4_rev_tuple(tx)],
        "gift_sigs": set(),
        "gift_revisions": [],
        "shares_set": {shares} if shares is not None else set(),
        "filing_dates": filing_dates,
        "orig_acceptance_ts": _f4_acceptance_ts(tx),
        "orig_accession": str(tx.get("accession") or "").strip() or None,
        "from_orig": from_orig,
        "reclassified": False,
    }


def _f4_entry_has_revision(
    entry: dict,
    *,
    dates: set[str] | None = None,
    shares_target: set[float] | None = None,
    price_target: set[float] | None = None,
    exact_shares: object = _UNSET,
    exact_price: object = _UNSET,
    include_gift_revisions: bool = False,
    gift_filing_date: str | None = None,
    gift_only: bool = False,
) -> bool:
    revs = [] if gift_only else entry["revisions"]
    if include_gift_revisions or gift_only:
        matching_gf = [rev[1:] for rev in entry["gift_revisions"] if gift_filing_date and rev[0] == gift_filing_date]
        revs = matching_gf or revs + [rev[1:] for rev in entry["gift_revisions"]]
    for rev_date, rev_shares, rev_price in revs:
        if dates is not None and rev_date not in dates:
            continue
        if shares_target is not None and not _f4_matches_num_set(rev_shares, shares_target):
            continue
        if price_target is not None and not _f4_matches_num_set(rev_price, price_target):
            continue
        if exact_shares is not _UNSET and rev_shares != exact_shares:
            continue
        if exact_price is not _UNSET and rev_price != exact_price:
            continue
        return True
    return False


def _f4_record_filing_provenance(ledger: list[dict], idx: int, tx: dict) -> None:
    fdate = str(tx.get("filing_date") or "").strip()
    if fdate:
        ledger[idx]["filing_dates"].add(fdate)


def _f4_mark_reclassified(ledger: list[dict], idx: int, tx: dict) -> None:
    entry = ledger[idx]
    entry["reclassified"] = True
    gfdate = str(tx.get("filing_date") or "").strip()
    entry["gift_sigs"].add((gfdate, _f4_exact_sig(tx)))
    grev = (gfdate, *_f4_rev_tuple(tx))
    if grev not in entry["gift_revisions"]:
        entry["gift_revisions"].append(grev)
    _f4_record_filing_provenance(ledger, idx, tx)


def _f4_update_entry(ledger: list[dict], idx: int, tx: dict, *, restore_if_reclassified: bool = False) -> None:
    entry = ledger[idx]
    entry["tx"] = tx
    entry["sigs"].add(_f4_exact_sig(tx))
    rev = _f4_rev_tuple(tx)
    if rev not in entry["revisions"]:
        entry["revisions"].append(rev)
    if tx.get("shares") is not None:
        entry["shares_set"].add(tx.get("shares"))
    _f4_record_filing_provenance(ledger, idx, tx)
    if restore_if_reclassified and entry["reclassified"]:
        entry["reclassified"] = False


def _f4_acceptance_ts(tx: dict) -> str:
    return _f4_normalize_acceptance_ts(tx.get("acceptance_datetime") or tx.get("acceptanceDateTime"))


def _f4_filed_on_or_before(filing_date: str, acceptance_ts: str, amend_date: str, amend_ts: str) -> bool:
    if not amend_date or not filing_date:
        return True
    if filing_date == amend_date and amend_ts and acceptance_ts:
        return acceptance_ts <= amend_ts
    return filing_date <= amend_date


def _f4_scoped_pool(
    ledger: list[dict],
    tx: dict,
    prior_len: int,
    *,
    ignore_tx_date: bool = False,
    prior_dates: set[str] | None = None,
    include_gift_revisions: bool = False,
) -> tuple[list[int], bool]:
    """Filter ledger indices by filing chronology and filing-date provenance.

    Originals filed strictly after the amendment are excluded even when `original_filing_date`
    is absent. Multi-accession ambiguity is intentionally NOT checked here; callers check
    `_f4_has_multi_accession_ambiguity` AFTER applying row-level evidence (shares, price,
    revision history) so distinct transactions sharing a filing-date chain remain linkable.
    """
    amend_fdate = str(tx.get("filing_date") or "").strip()
    amend_ts = _f4_acceptance_ts(tx)

    def _filed_on_or_before(entry: dict) -> bool:
        earliest = min(entry["filing_dates"], default="")
        return _f4_filed_on_or_before(earliest, entry["orig_acceptance_ts"], amend_fdate, amend_ts)

    orig_date = str(tx.get("original_filing_date") or "").strip()
    if not orig_date or not any(ledger[i]["filing_dates"] for i in range(prior_len)):
        pool = [i for i in range(prior_len) if _filed_on_or_before(ledger[i])]
    else:
        pool = [
            i for i in range(prior_len) if orig_date in ledger[i]["filing_dates"] and _filed_on_or_before(ledger[i])
        ]
        if not pool:
            return [], True
    if ignore_tx_date and prior_dates:
        osig = _f4_owner_sec_sig(tx)
        eligible = [
            i
            for i in pool
            if _f4_owner_sec_sig(ledger[i]["tx"]) == osig
            and _f4_entry_has_revision(
                ledger[i],
                dates=prior_dates,
                include_gift_revisions=include_gift_revisions,
                gift_filing_date=orig_date or None,
            )
        ]
        if not eligible:
            return [], True
        return eligible, False
    return pool, False


def _f4_has_multi_accession_ambiguity(ledger: list[dict], tx: dict, cands: list[int]) -> bool:
    if not str(tx.get("original_filing_date") or "").strip():
        return False
    return len({ledger[i]["orig_accession"] for i in cands if ledger[i]["orig_accession"]}) > 1


def _f4_sort_amendment_accessions(all_amend: list[dict]) -> tuple[list[tuple[str, list[dict]]], list[dict]]:
    """Group Form 4/A rows by accession and sort by (filing_date, EDGAR acceptanceDateTime, accession)."""
    amend_by_acc: dict[str, list[dict]] = {}
    for tx in all_amend:
        acc = str(tx.get("accession") or f"__amend_{id(tx)}")
        amend_by_acc.setdefault(acc, []).append(tx)

    acc_meta: dict[str, tuple[str, str]] = {}
    same_day_groups: dict[tuple[tuple, str], list[str]] = {}
    for acc, rows in amend_by_acc.items():
        fdate = min(str(r.get("filing_date") or "") for r in rows)
        acc_ts = min((ts for r in rows if (ts := _f4_acceptance_ts(r))), default="")
        acc_meta[acc] = (fdate, acc_ts)
        for ok in {_f4_owner_key(r) for r in rows}:
            same_day_groups.setdefault((ok, fdate), []).append(acc)

    def _same_submitter_prefix(acc_a: str, acc_b: str) -> bool:
        return "-" in acc_a and "-" in acc_b and acc_a.split("-", 1)[0] == acc_b.split("-", 1)[0]

    uncertain_txs: list[dict] = []
    for (_ok, _fdate), accs in same_day_groups.items():
        if len(accs) < 2:
            continue
        ambig_accs: set[str] = set()
        for i in range(len(accs)):
            for j in range(i + 1, len(accs)):
                a, b = accs[i], accs[j]
                ts_a, ts_b = acc_meta[a][1], acc_meta[b][1]
                if bool(ts_a) != bool(ts_b) or (ts_a == ts_b and not _same_submitter_prefix(a, b)):
                    ambig_accs.update((a, b))
        for a in ambig_accs:
            uncertain_txs.extend(amend_by_acc[a])

    sorted_items = sorted(
        amend_by_acc.items(),
        key=lambda item: (acc_meta[item[0]][0], acc_meta[item[0]][1], item[0]),
    )
    return sorted_items, uncertain_txs


def _f4_non_sell_revisions(history: list[dict], linked_date: str, amend_date: str, amend_ts: str) -> list[dict]:
    """Return revisions identified by the filing link and filed by the amendment time."""
    original_date = str(history[0].get("filing_date") or "").strip()
    links_chain = not linked_date or not original_date or linked_date == original_date
    revisions = []
    for rev in history:
        revision_date = str(rev.get("filing_date") or "").strip()
        if (links_chain or revision_date == linked_date) and _f4_filed_on_or_before(
            revision_date, _f4_acceptance_ts(rev), amend_date, amend_ts
        ):
            revisions.append(rev)
    return revisions


def _f4_prior_native_gifts(
    non_sell_histories: list[list[dict]],
    tx: dict,
    linked_date: str,
    *,
    shares_target: set[float] | None = None,
    price_target: set[float] | None = None,
) -> list[tuple[int, list[dict]]]:
    base = _f4_base_sig(tx)
    filing_date = str(tx.get("filing_date") or "").strip()
    acceptance_ts = _f4_acceptance_ts(tx)
    gifts = []
    for i, history in enumerate(non_sell_histories):
        if _f4_tx_code(history[0]) != "G" or _f4_base_sig(history[0]) != base:
            continue
        revisions = [
            rev
            for rev in _f4_non_sell_revisions(history, linked_date, filing_date, acceptance_ts)
            if (not shares_target or _f4_matches_num_set(rev.get("shares"), shares_target))
            and (not price_target or _f4_matches_num_set(rev.get("price"), price_target))
        ]
        if revisions:
            gifts.append((i, revisions))
    return gifts


def _f4_prior_reclassified_gifts(
    ledger: list[dict], tx: dict, pool: list[int], prior_shares: set[float], prior_prices: set[float]
) -> list[int]:
    base = _f4_base_sig(tx)
    linked_date = str(tx.get("original_filing_date") or "").strip() or None
    return [
        i
        for i in pool
        if ledger[i]["reclassified"]
        and _f4_base_sig(ledger[i]["tx"]) == base
        and _f4_entry_has_revision(
            ledger[i],
            shares_target=prior_shares or None,
            price_target=prior_prices or None,
            gift_filing_date=linked_date,
            gift_only=True,
        )
    ]


def _f4_reconcile_non_sells(
    ledger: list[dict],
    non_sell_histories: list[list[dict]],
    acc_non_sells: list[tuple[dict, tuple[bool, bool], bool, str, bool]],
    prior_len: int,
    mark_uncertain,
) -> tuple[set[int], list[tuple[int, dict]]]:
    acc_orig_ns_matched: set[int] = set()
    acc_matched_ns: set[int] = set()
    pending_reclassifications: list[tuple[int, dict]] = []
    native_gift_claims: dict[int, list[dict]] = {}
    revision_claims: dict[int, list[dict]] = {}
    prior_gift_claims: set[int] = set()

    def reserve_target(target: int, tx: dict) -> None:
        if ledger[target]["reclassified"]:
            revision_claims.setdefault(target, []).append(tx)
        else:
            acc_matched_ns.add(target)
            pending_reclassifications.append((target, tx))

    def prefer_active_sales(candidates: list[int], text: str) -> list[int]:
        # Prefer an active sale only when an earlier matching pass identified the existing gift separately.
        if prior_gift_claims.intersection(candidates) and any(
            _F4_EXPLICIT_SALE_RECLASS_RE.match(sentence) for sentence in _f4_statements(text)
        ):
            return [i for i in candidates if not ledger[i]["reclassified"]] or candidates
        return candidates

    acc_added_non_sells: list[dict] = []
    ns_candidates: list[tuple[dict, bool, bool, str]] = []
    for tx, (is_add, is_corr), is_ambig, rtext, unresolved_conversion in acc_non_sells:
        base = _f4_base_sig(tx)
        code = _f4_tx_code(tx)
        shares = tx.get("shares")
        orig_date = str(tx.get("original_filing_date") or "").strip()
        has_identity = base[0] != ("",) and bool(base[1]) and shares is not None
        pool, pool_unlinked = _f4_scoped_pool(ledger, tx, prior_len)
        sentences = _f4_statements(rtext)
        explicit_sale_conversion = any(_F4_EXPLICIT_SALE_RECLASS_RE.match(sentence) for sentence in sentences)
        is_corr = is_corr or explicit_sale_conversion
        is_explicit_add = is_add and not is_corr
        gift_corrections = [sentence for sentence in sentences if _F4_GIFT_CORRECTION_RE.search(sentence)]
        if gift_corrections and not explicit_sale_conversion:
            # The new quantity or price identifies the revision, not the transaction being corrected.
            correction_text = ". ".join(gift_corrections)
            prior_shares = _f4_stated_prior_shares(correction_text, shares)
            prior_prices = _f4_stated_prior_prices(correction_text, tx.get("price"))
            native_gifts = []
            gift_candidates = []
            if has_identity:
                native_gifts = _f4_prior_native_gifts(
                    non_sell_histories, tx, orig_date, shares_target=prior_shares, price_target=prior_prices
                )
                gift_candidates = _f4_prior_reclassified_gifts(ledger, tx, pool, prior_shares, prior_prices)
            if len(native_gifts) == 1 and not gift_candidates:
                native_gift_claims.setdefault(native_gifts[0][0], []).append(tx)
            elif not native_gifts and len(gift_candidates) == 1:
                reserve_target(gift_candidates[0], tx)
            else:
                mark_uncertain(tx)
            continue
        if unresolved_conversion and not (explicit_sale_conversion or is_explicit_add):
            # A quantity match cannot stand in for the unresolved footnote.
            continue
        is_sale_conversion = explicit_sale_conversion or (is_corr and bool(_F4_SALE_TO_NONSELL_RE.search(rtext)))
        if code == "G" and has_identity and not (is_explicit_add or is_ambig or is_sale_conversion):
            exact = _f4_exact_sig(tx)
            exact_native_gifts = [
                i
                for i, revisions in _f4_prior_native_gifts(non_sell_histories, tx, orig_date)
                if any(_f4_tx_code(rev) == code and _f4_exact_sig(rev) == exact for rev in revisions)
            ]
            if len(exact_native_gifts) == 1 and not any(
                ledger[i]["reclassified"]
                and _f4_base_sig(ledger[i]["tx"]) == base
                and _f4_entry_has_revision(
                    ledger[i],
                    dates={_f4_rev_tuple(tx)[0]},
                    exact_shares=shares,
                    exact_price=tx.get("price"),
                    gift_filing_date=orig_date,
                    gift_only=True,
                )
                for i in pool
            ):
                target = exact_native_gifts[0]
                native_gift_claims.setdefault(target, []).append(tx)
                acc_orig_ns_matched.add(target)
                continue
        ns_idx = None
        matching_revisions = []
        filing_date = str(tx.get("filing_date") or "").strip()
        acceptance_ts = _f4_acceptance_ts(tx)
        for i, history in enumerate(non_sell_histories):
            if i in acc_orig_ns_matched or _f4_base_sig(history[0]) != base:
                continue
            eligible = history[:1] if is_explicit_add else history
            matching_revisions = [
                rev
                for rev in _f4_non_sell_revisions(eligible, orig_date, filing_date, acceptance_ts)
                if _f4_tx_code(rev) == code and (shares is None or rev.get("shares") == shares)
            ]
            if matching_revisions:
                ns_idx = i
                break
        if ns_idx is not None:
            priced_sell_match = (
                not pool_unlinked
                and has_identity
                and tx.get("price") is not None
                and all(rev.get("price") != tx.get("price") for rev in matching_revisions)
                and any(i not in acc_matched_ns and _f4_exact_sig(tx) in ledger[i]["sigs"] for i in pool)
            )
            has_ledger_sell = not pool_unlinked and any(
                i not in acc_matched_ns and _f4_base_sig(ledger[i]["tx"]) == base for i in pool
            )
            if priced_sell_match or (has_ledger_sell and is_sale_conversion):
                ns_candidates.append((tx, is_add, is_corr, rtext))
                continue
            acc_orig_ns_matched.add(ns_idx)
            continue
        if is_explicit_add:
            acc_added_non_sells.append(tx)
            continue
        if pool_unlinked:
            mark_uncertain(tx)
            continue
        ns_candidates.append((tx, is_add, is_corr, rtext))
    prior_gift_claims.update(revision_claims)

    # Pass A0: explicit corrections stating prior shares/price resolve FIRST in order-independent batch waves
    # so specific claims reserve their target before broader claims retry, and new values never hijack unrelated sales.
    a0_rows: list[tuple[int, dict, str, set[float], set[float]]] = []
    after_ns_a0: list[tuple[dict, bool, bool, str]] = []
    for idx_ns, (tx, is_add, is_corr, rtext) in enumerate(ns_candidates):
        base = _f4_base_sig(tx)
        shares = tx.get("shares")
        prior_shares = _f4_stated_prior_shares(rtext, shares) if is_corr else set()
        prior_prices = _f4_stated_prior_prices(rtext, tx.get("price")) if is_corr else set()
        if is_corr and (prior_shares or prior_prices) and base[0] != ("",) and bool(base[1]):
            a0_rows.append((idx_ns, tx, rtext, prior_shares, prior_prices))
        else:
            after_ns_a0.append((tx, is_add, is_corr, rtext))

    def _a0_cands(tx: dict, text: str, prior_shares: set[float], prior_prices: set[float]) -> tuple[list[int], bool]:
        pool, pool_unlinked = _f4_scoped_pool(ledger, tx, prior_len)
        if pool_unlinked:
            return [], True
        base = _f4_base_sig(tx)
        orig_date = str(tx.get("original_filing_date") or "").strip() or None
        cands = [
            i
            for i in pool
            if i not in acc_matched_ns
            and _f4_base_sig(ledger[i]["tx"]) == base
            and _f4_entry_has_revision(
                ledger[i],
                shares_target=prior_shares or None,
                price_target=prior_prices or None,
                include_gift_revisions=ledger[i]["reclassified"],
                gift_filing_date=orig_date,
            )
        ]
        cands = prefer_active_sales(cands, text)
        if cands and _f4_has_multi_accession_ambiguity(ledger, tx, cands):
            return [], True
        return cands, False

    resolved_a0: set[int] = set()
    while True:
        single_a0_claims: dict[int, list[tuple[int, dict]]] = {}
        for idx_ns, tx, rtext, prior_shares, prior_prices in a0_rows:
            if idx_ns in resolved_a0:
                continue
            cands, is_ambig = _a0_cands(tx, rtext, prior_shares, prior_prices)
            if not is_ambig and len(cands) == 1:
                single_a0_claims.setdefault(cands[0], []).append((idx_ns, tx))
        progressed = False
        for target, claimants in single_a0_claims.items():
            if len(claimants) == 1:
                idx_ns, tx = claimants[0]
                reserve_target(target, tx)
                resolved_a0.add(idx_ns)
                progressed = True
        if not progressed:
            break
        prior_gift_claims.update(revision_claims)

    for idx_ns, tx, rtext, prior_shares, prior_prices in a0_rows:
        if idx_ns in resolved_a0:
            continue
        cands, is_ambig = _a0_cands(tx, rtext, prior_shares, prior_prices)
        if not is_ambig:
            already_reclass = [i for i in cands if ledger[i]["reclassified"]]
            if len(already_reclass) == 1:
                reserve_target(already_reclass[0], tx)
                continue
        mark_uncertain(tx)
    prior_gift_claims.update(revision_claims)

    # Pass A1: exact (base, shares, price) matches first (independent of row order within accession).
    after_ns_a1: list[tuple[dict, bool, bool, str]] = []
    for tx, is_add, is_corr, rtext in after_ns_a0:
        base = _f4_base_sig(tx)
        shares = tx.get("shares")
        has_identity = base[0] != ("",) and bool(base[1]) and shares is not None
        pool, pool_unlinked = _f4_scoped_pool(ledger, tx, prior_len)
        if pool_unlinked:
            mark_uncertain(tx)
            continue
        if has_identity and tx.get("price") is not None:
            exact = _f4_exact_sig(tx)
            exact_cands = [i for i in pool if i not in acc_matched_ns and exact in ledger[i]["sigs"]]
            if exact_cands:
                exact_cands = prefer_active_sales(exact_cands, rtext)
                cur_exact = [i for i in exact_cands if _f4_exact_sig(ledger[i]["tx"]) == exact]
                if cur_exact:
                    exact_cands = cur_exact
                if _f4_has_multi_accession_ambiguity(ledger, tx, exact_cands):
                    mark_uncertain(tx)
                    continue
                reclass_exact = [i for i in exact_cands if ledger[i]["reclassified"]]
                reserve_target(reclass_exact[0] if reclass_exact else exact_cands[0], tx)
                continue
        after_ns_a1.append((tx, is_add, is_corr, rtext))
    prior_gift_claims.update(revision_claims)

    # Pass A2: unique (base, shares) matches (or uniform-price candidates) after exact matches are reserved.
    deferred_ns: list[tuple[dict, bool, bool, str]] = []
    for tx, is_add, is_corr, rtext in after_ns_a1:
        base = _f4_base_sig(tx)
        shares = tx.get("shares")
        has_identity = base[0] != ("",) and bool(base[1]) and shares is not None
        pool, _ = _f4_scoped_pool(ledger, tx, prior_len)
        if has_identity:
            share_cands = [
                i
                for i in pool
                if i not in acc_matched_ns
                and _f4_base_sig(ledger[i]["tx"]) == base
                and shares in ledger[i]["shares_set"]
            ]
            share_cands = prefer_active_sales(share_cands, rtext)
            if share_cands and _f4_has_multi_accession_ambiguity(ledger, tx, share_cands):
                mark_uncertain(tx)
                continue
            if len(share_cands) == 1 or (
                len(share_cands) > 1 and len({ledger[i]["tx"].get("price") for i in share_cands}) == 1
            ):
                reclass_cands = [i for i in share_cands if ledger[i]["reclassified"]]
                reserve_target(reclass_cands[0] if reclass_cands else share_cands[0], tx)
                continue
        deferred_ns.append((tx, is_add, is_corr, rtext))
    prior_gift_claims.update(revision_claims)

    # Pass B: remaining blank-price links and explicit share-changing corrections.
    for tx, _is_add, is_corr, rtext in deferred_ns:
        base = _f4_base_sig(tx)
        shares = tx.get("shares")
        has_identity = base[0] != ("",) and bool(base[1]) and shares is not None
        pool, _ = _f4_scoped_pool(ledger, tx, prior_len)
        if has_identity:
            share_cands = [
                i
                for i in pool
                if i not in acc_matched_ns
                and _f4_base_sig(ledger[i]["tx"]) == base
                and shares in ledger[i]["shares_set"]
            ]
            share_cands = prefer_active_sales(share_cands, rtext)
            if share_cands and _f4_has_multi_accession_ambiguity(ledger, tx, share_cands):
                mark_uncertain(tx)
                continue
            if len(share_cands) == 1:
                reserve_target(share_cands[0], tx)
                continue
            if len(share_cands) > 1:
                already_reclass = [i for i in share_cands if ledger[i]["reclassified"]]
                if len(already_reclass) == 1:
                    reserve_target(already_reclass[0], tx)
                    continue
                mark_uncertain(tx)
                continue
        if is_corr and base[0] != ("",) and bool(base[1]):
            base_cands = [i for i in pool if i not in acc_matched_ns and _f4_base_sig(ledger[i]["tx"]) == base]
            base_cands = prefer_active_sales(base_cands, rtext)
            if base_cands and _f4_has_multi_accession_ambiguity(ledger, tx, base_cands):
                mark_uncertain(tx)
                continue
            if len(base_cands) == 1:
                reserve_target(base_cands[0], tx)
                continue
            already_reclass = [i for i in base_cands if ledger[i]["reclassified"]]
            if len(already_reclass) == 1:
                reserve_target(already_reclass[0], tx)
                continue
            mark_uncertain(tx)
            continue
        if not has_identity:
            mark_uncertain(tx)

    # An existing gift can attract claims at different match strengths within the same filing.
    for target, claimants in native_gift_claims.items():
        if len(claimants) == 1:
            non_sell_histories[target].append(claimants[0])
        else:
            for tx in claimants:
                mark_uncertain(tx)
    for target, claimants in revision_claims.items():
        acc_matched_ns.add(target)
        if len(claimants) == 1:
            pending_reclassifications.append((target, claimants[0]))
        else:
            for tx in claimants:
                mark_uncertain(tx)
    non_sell_histories.extend([tx] for tx in acc_added_non_sells)
    return acc_matched_ns, pending_reclassifications


def _f4_reconcile_sells(
    ledger: list[dict],
    acc_sells: list[tuple[dict, tuple[bool, bool], bool, str]],
    prior_len: int,
    mark_uncertain,
    *,
    excluded_entries: set[int] | None = None,
) -> None:
    acc_matched_sell: set[int] = set(excluded_entries or ())
    acc_explicit_adds: list[dict] = []
    sell_candidates: list[tuple[dict, bool, str]] = []
    for tx, (is_add, is_corr), _is_ambig, rtext in acc_sells:
        if is_add and not is_corr:
            acc_explicit_adds.append(tx)
        else:
            sell_candidates.append((tx, is_corr, rtext))

    # Sub-pass 1: exact signature matches in two order-independent batch phases (current-sig before historical alias).
    after_exact: list[tuple[dict, bool, str]] = []
    exact_batch: list[tuple[int, dict, bool, str, list[int]]] = []
    for idx_s, (tx, is_corr, rtext) in enumerate(sell_candidates):
        corr_td = str(tx.get("transaction_date") or tx.get("date") or "").strip()[:10]
        has_explicit_prior_date = (
            is_corr
            and _f4_has_date_corr_evidence(tx, rtext)
            and bool({d for d in _F4_ISO_DATE_RE.findall(rtext) if d != corr_td})
        )
        has_explicit_prior_shares = is_corr and bool(_f4_stated_prior_shares(rtext, tx.get("shares")))
        has_explicit_prior_price = is_corr and bool(_f4_stated_prior_prices(rtext, tx.get("price")))
        is_field_corr = (
            is_corr
            and not _F4_RESTORE_SELL_RE.search(rtext)
            and (_f4_has_amount_corr_evidence(tx, rtext) or _f4_has_date_corr_evidence(tx, rtext))
        )
        if has_explicit_prior_date or has_explicit_prior_shares or has_explicit_prior_price or is_field_corr:
            after_exact.append((tx, is_corr, rtext))
            continue
        pool, pool_unlinked = _f4_scoped_pool(ledger, tx, prior_len)
        if pool_unlinked:
            mark_uncertain(tx)
            continue
        exact_batch.append((idx_s, tx, is_corr, rtext, pool))

    resolved_exact: set[int] = set()
    uncertain_exact: set[int] = set()

    def _exact_phase(use_history: bool) -> None:
        single_claims: dict[int, list[tuple[int, dict, bool, bool, bool]]] = {}
        sym_restatements: dict[tuple[int, ...], list[tuple[int, dict]]] = {}
        for idx_s, tx, is_corr, rtext, pool in exact_batch:
            if idx_s in resolved_exact or idx_s in uncertain_exact:
                continue
            exact = _f4_exact_sig(tx)
            orig_date = str(tx.get("original_filing_date") or "").strip() or None
            is_restore_intent = is_corr and bool(_F4_RESTORE_SELL_RE.search(rtext))

            def _matches_gift_sig(entry: dict, *, sig=exact, gfd=orig_date) -> bool:
                if not entry["gift_sigs"]:
                    return False
                if gfd:
                    on_date = [gsig for fd, gsig in entry["gift_sigs"] if fd == gfd]
                    if on_date:
                        return sig in on_date
                return any(gsig == sig for _fd, gsig in entry["gift_sigs"])

            if use_history:
                cands = [
                    i
                    for i in pool
                    if i not in acc_matched_sell
                    and (exact in ledger[i]["sigs"] or (is_restore_intent and _matches_gift_sig(ledger[i])))
                ]
            else:
                cands = [
                    i
                    for i in pool
                    if i not in acc_matched_sell
                    and (
                        _f4_exact_sig(ledger[i]["tx"]) == exact or (is_restore_intent and _matches_gift_sig(ledger[i]))
                    )
                ]
            if not cands:
                continue
            if is_restore_intent:
                reclass_cands = [i for i in cands if ledger[i]["reclassified"]]
                if reclass_cands and _f4_has_multi_accession_ambiguity(ledger, tx, reclass_cands):
                    mark_uncertain(tx)
                    uncertain_exact.add(idx_s)
                elif len(reclass_cands) == 1:
                    single_claims.setdefault(reclass_cands[0], []).append((idx_s, tx, True, False, True))
                elif len(reclass_cands) > 1:
                    mark_uncertain(tx)
                    uncertain_exact.add(idx_s)
            else:
                active_cands = [i for i in cands if not ledger[i]["reclassified"]]
                if active_cands and _f4_has_multi_accession_ambiguity(ledger, tx, active_cands):
                    mark_uncertain(tx)
                    uncertain_exact.add(idx_s)
                elif len(active_cands) == 1:
                    do_update = (not use_history) or is_corr
                    single_claims.setdefault(active_cands[0], []).append((idx_s, tx, False, False, do_update))
                elif len(active_cands) > 1 and not use_history:
                    sym_restatements.setdefault(tuple(sorted(active_cands)), []).append((idx_s, tx))
                elif len(active_cands) > 1:
                    mark_uncertain(tx)
                    uncertain_exact.add(idx_s)
                elif not is_corr:
                    if _f4_has_multi_accession_ambiguity(ledger, tx, cands):
                        mark_uncertain(tx)
                        uncertain_exact.add(idx_s)
                    elif len(cands) == 1:
                        single_claims.setdefault(cands[0], []).append((idx_s, tx, False, True, False))
                    else:
                        mark_uncertain(tx)
                        uncertain_exact.add(idx_s)

        claimed_targets: set[int] = set()
        for target, claimants in single_claims.items():
            if len(claimants) == 1:
                idx_s, tx, do_restore, mark_unc, do_update = claimants[0]
                acc_matched_sell.add(target)
                claimed_targets.add(target)
                if mark_unc:
                    mark_uncertain(tx)
                elif do_update:
                    _f4_update_entry(ledger, target, tx, restore_if_reclassified=do_restore)
                else:
                    _f4_record_filing_provenance(ledger, target, tx)
                resolved_exact.add(idx_s)
            else:
                for idx_s, tx, _do_restore, _mark_unc, _do_update in claimants:
                    mark_uncertain(tx)
                    uncertain_exact.add(idx_s)

        if sym_restatements:
            sym_items: list[tuple[int, dict, tuple[int, ...], tuple[int, ...]]] = []
            grp_avail: dict[tuple[int, ...], tuple[int, ...]] = {}
            for slots, claimants in sorted(sym_restatements.items(), key=lambda kv: (len(kv[0]), kv[0])):
                avail = tuple(s for s in slots if s not in claimed_targets and s not in acc_matched_sell)
                grp_avail[slots] = avail
                for idx_s, tx in claimants:
                    sym_items.append((idx_s, tx, avail, slots))

            slot_owner: dict[int, int] = {}

            def _augment(item_pos: int, visited: set[int], *, items=sym_items, owners=slot_owner) -> bool:
                for slot in items[item_pos][2]:
                    if slot in visited:
                        continue
                    visited.add(slot)
                    prev = owners.get(slot)
                    if prev is None or _augment(prev, visited):
                        owners[slot] = item_pos
                        return True
                return False

            for pos in range(len(sym_items)):
                _augment(pos, set())

            assigned_positions = set(slot_owner.values())
            grp_assigned: dict[tuple[int, ...], list[tuple[int, int, dict]]] = {}
            for slot, pos in slot_owner.items():
                idx_s, tx, _avail, grp_key = sym_items[pos]
                acc_matched_sell.add(slot)
                resolved_exact.add(idx_s)
                grp_assigned.setdefault(grp_key, []).append((slot, idx_s, tx))

            for grp_key, assigned_rows in grp_assigned.items():
                taken_elsewhere = {slot for slot, p in slot_owner.items() if sym_items[p][3] != grp_key}
                effective_avail = [s for s in grp_avail[grp_key] if s not in taken_elsewhere]
                sample_tx = assigned_rows[0][2]
                for s in effective_avail:
                    _f4_record_filing_provenance(ledger, s, sample_tx)
                if len(assigned_rows) < len(effective_avail):
                    for _slot, _idx_s, tx in assigned_rows:
                        mark_uncertain(tx)

            for pos, (idx_s, tx, avail, _grp_key) in enumerate(sym_items):
                if pos in assigned_positions:
                    continue
                exact = _f4_exact_sig(tx)
                other_unmatched = any(
                    i not in acc_matched_sell
                    and not ledger[i]["reclassified"]
                    and _f4_exact_sig(ledger[i]["tx"]) == exact
                    for i in range(prior_len)
                )
                if other_unmatched or not avail:
                    mark_uncertain(tx)
                    uncertain_exact.add(idx_s)

    _exact_phase(use_history=False)
    _exact_phase(use_history=True)
    for idx_s, tx, is_corr, rtext, _pool in exact_batch:
        if idx_s not in resolved_exact and idx_s not in uncertain_exact:
            after_exact.append((tx, is_corr, rtext))

    # Sub-pass 2: explicit corrections (price, share, restoration, and date corrections) in order-independent waves.
    acc_new_sales: list[dict] = list(acc_explicit_adds)
    corr_rows = [(idx, tx, rtext) for idx, (tx, is_corr, rtext) in enumerate(after_exact) if is_corr]
    unannotated_rows = [(idx, tx, rtext) for idx, (tx, is_corr, rtext) in enumerate(after_exact) if not is_corr]

    def _corr_match_cands(tx: dict, rtext: str) -> tuple[list[int], bool, bool, bool]:
        b = _f4_base_sig(tx)
        if b[0] == ("",) or not bool(b[1]):
            return [], False, False, False
        is_restore_intent = bool(_F4_RESTORE_SELL_RE.search(rtext))
        corr_td = str(tx.get("transaction_date") or tx.get("date") or "").strip()[:10]
        has_date_corr = _f4_has_date_corr_evidence(tx, rtext)
        prior_dates = {d for d in _F4_ISO_DATE_RE.findall(rtext) if d != corr_td} if has_date_corr else set()
        prior_prices = _f4_stated_prior_prices(rtext, tx.get("price"))
        prior_shares = _f4_stated_prior_shares(rtext, tx.get("shares"))
        shares = tx.get("shares")
        price = tx.get("price")

        orig_date = str(tx.get("original_filing_date") or "").strip() or None

        if not prior_dates:
            pool, pool_unlinked = _f4_scoped_pool(ledger, tx, prior_len)
            if not pool_unlinked:
                same_base = [
                    i
                    for i in pool
                    if i not in acc_matched_sell
                    and _f4_base_sig(ledger[i]["tx"]) == b
                    and (is_restore_intent or not ledger[i]["reclassified"])
                ]
                if is_restore_intent:
                    reclass_only = [i for i in same_base if ledger[i]["reclassified"]]
                    if reclass_only:
                        same_base = reclass_only
                if prior_shares or prior_prices:
                    unchanged_shares = shares if (not prior_shares and shares is not None) else _UNSET
                    cands = [
                        i
                        for i in same_base
                        if _f4_entry_has_revision(
                            ledger[i],
                            shares_target=prior_shares or None,
                            price_target=prior_prices or None,
                            exact_shares=unchanged_shares,
                            include_gift_revisions=is_restore_intent,
                            gift_filing_date=orig_date,
                        )
                    ]
                    if _f4_has_multi_accession_ambiguity(ledger, tx, cands):
                        return [], is_restore_intent, False, False
                    return cands, is_restore_intent, True, False
                if shares is not None:
                    share_matched = [
                        i
                        for i in same_base
                        if _f4_entry_has_revision(
                            ledger[i],
                            exact_shares=shares,
                            include_gift_revisions=is_restore_intent,
                            gift_filing_date=orig_date,
                        )
                    ]
                    if share_matched:
                        same_base = share_matched
                if _f4_has_multi_accession_ambiguity(ledger, tx, same_base):
                    return [], is_restore_intent, False, False
                if same_base:
                    return same_base, is_restore_intent, False, False

        if has_date_corr:
            dpool, dpool_unlinked = _f4_scoped_pool(
                ledger,
                tx,
                prior_len,
                ignore_tx_date=True,
                prior_dates=prior_dates or None,
                include_gift_revisions=is_restore_intent,
            )
            if dpool_unlinked:
                return [], is_restore_intent, False, True
            osig = _f4_owner_sec_sig(tx)
            date_cands = [
                i
                for i in dpool
                if i not in acc_matched_sell
                and _f4_owner_sec_sig(ledger[i]["tx"]) == osig
                and (is_restore_intent or not ledger[i]["reclassified"])
            ]
            if prior_dates or prior_shares or prior_prices:
                date_cands = [
                    i
                    for i in date_cands
                    if _f4_entry_has_revision(
                        ledger[i],
                        dates=prior_dates or None,
                        shares_target=prior_shares or None,
                        price_target=prior_prices or None,
                        include_gift_revisions=is_restore_intent,
                        gift_filing_date=orig_date,
                    )
                ]
            if len(date_cands) > 1 and shares is not None and price is not None and not (prior_shares or prior_prices):
                by_sp = [
                    i
                    for i in date_cands
                    if _f4_entry_has_revision(
                        ledger[i],
                        dates=prior_dates or None,
                        exact_shares=shares,
                        exact_price=price,
                        include_gift_revisions=is_restore_intent,
                        gift_filing_date=orig_date,
                    )
                ]
                if by_sp:
                    date_cands = by_sp
            if len(date_cands) > 1 and shares is not None and not prior_shares:
                by_s = [
                    i
                    for i in date_cands
                    if _f4_entry_has_revision(
                        ledger[i],
                        dates=prior_dates or None,
                        exact_shares=shares,
                        include_gift_revisions=is_restore_intent,
                        gift_filing_date=orig_date,
                    )
                ]
                if by_s:
                    date_cands = by_s
            if _f4_has_multi_accession_ambiguity(ledger, tx, date_cands):
                return [], is_restore_intent, False, True
            return date_cands, is_restore_intent, bool(prior_shares or prior_prices), True
        return [], is_restore_intent, False, False

    resolved_corr: set[int] = set()
    while True:
        single_corr_claims: dict[int, list[tuple[int, dict, bool, bool, bool]]] = {}
        for ridx, tx, rtext in corr_rows:
            if ridx in resolved_corr:
                continue
            cands, is_restore_intent, identified_prior, is_date_match = _corr_match_cands(tx, rtext)
            if len(cands) == 1:
                single_corr_claims.setdefault(cands[0], []).append(
                    (ridx, tx, is_restore_intent, identified_prior, is_date_match)
                )
        progressed = False
        for target, claimants in single_corr_claims.items():
            if len(claimants) == 1:
                ridx, tx, is_restore_intent, identified_prior, is_date_match = claimants[0]
                was_reclassified = ledger[target]["reclassified"]
                same_sp = tx.get("shares") is not None and _f4_entry_has_revision(
                    ledger[target], exact_shares=tx.get("shares"), exact_price=tx.get("price")
                )
                acc_matched_sell.add(target)
                _f4_update_entry(ledger, target, tx, restore_if_reclassified=is_restore_intent)
                if not (identified_prior or (is_restore_intent and was_reclassified) or (is_date_match and same_sp)):
                    mark_uncertain(tx)
                resolved_corr.add(ridx)
                progressed = True
        if not progressed:
            break

    for ridx, tx, _rtext in corr_rows:
        if ridx not in resolved_corr:
            mark_uncertain(tx)

    # Sub-pass 3: heuristic unannotated share-matched restatements (after explicit corrections).
    def _heuristic_share_cands(tx: dict) -> tuple[list[int], bool]:
        b = _f4_base_sig(tx)
        shares = tx.get("shares")
        if shares is None or b[0] == ("",) or not bool(b[1]):
            return [], False
        pool, pool_unlinked = _f4_scoped_pool(ledger, tx, prior_len)
        if pool_unlinked:
            return [], True
        cands = [
            i
            for i in pool
            if i not in acc_matched_sell and _f4_base_sig(ledger[i]["tx"]) == b and shares in ledger[i]["shares_set"]
        ]
        if cands and _f4_has_multi_accession_ambiguity(ledger, tx, cands):
            return [], True
        return cands, False

    resolved_heur: set[int] = set()
    while True:
        single_heur_claims: dict[int, list[tuple[int, dict]]] = {}
        for ridx, tx, _rtext in unannotated_rows:
            if ridx in resolved_heur:
                continue
            cands, is_ambig = _heuristic_share_cands(tx)
            if not is_ambig and len(cands) == 1:
                single_heur_claims.setdefault(cands[0], []).append((ridx, tx))
        progressed = False
        for target, claimants in single_heur_claims.items():
            if len(claimants) == 1:
                ridx, tx = claimants[0]
                acc_matched_sell.add(target)
                _f4_update_entry(ledger, target, tx)
                mark_uncertain(tx)
                resolved_heur.add(ridx)
                progressed = True
        if not progressed:
            break

    # Matching all same-day sales cannot rule out a date correction or a repeated gift row.
    for ridx, tx, _rtext in unannotated_rows:
        if ridx not in resolved_heur:
            mark_uncertain(tx)

    # Merge newly disclosed sales from this accession idempotently against existing 4/A-added ledger entries.
    acc_new_counts: dict[tuple, list[dict]] = {}
    for tx in acc_new_sales:
        acc_new_counts.setdefault(_f4_exact_sig(tx), []).append(tx)
    existing_counts: dict[tuple, int] = {}
    for entry in ledger:
        if not entry["from_orig"]:
            for sig in entry["sigs"]:
                existing_counts[sig] = existing_counts.get(sig, 0) + 1
    for e, rows in acc_new_counts.items():
        need = len(rows) - existing_counts.get(e, 0)
        if need > 0:
            ledger.extend(_f4_make_ledger_entry(tx, from_orig=False) for tx in rows[:need])


def check_us_insider_trades(symbol: str, *, run=None) -> dict:
    if not os.environ.get("SEC_USER_AGENT"):
        return {
            "sales_90d": None,
            "flags": [],
            "available": False,
            "configured": False,
            "truncated": False,
            "warnings": [],
        }
    data = (run or _run_tool)("sec_edgar.py", ["insider_trades", symbol, "--days", "90", "--top", "10"], timeout=30)
    result = {"sales_90d": 0, "flags": [], "available": False, "configured": True, "truncated": False, "warnings": []}
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get("transactions"), list):
        return result
    truncated = bool(data.get("truncated"))
    sec_warnings = [str(w) for w in (data.get("warnings") or []) if w]
    result["truncated"] = truncated
    result["warnings"] = sec_warnings

    def _is_amendment_advisory(warning: str) -> bool:
        low = warning.lower()
        return "amendment" in low and "do not double-count" in low and "unavailable" not in low

    retrieval_warnings = [w for w in sec_warnings if not _is_amendment_advisory(w)]
    result["available"] = not truncated and not retrieval_warnings
    txs = [tx for tx in data["transactions"] if isinstance(tx, dict)]

    raw_cutoff = str(data.get("since_filing_date") or "").strip()[:10]
    try:
        tx_cutoff = date.fromisoformat(raw_cutoff).isoformat() if raw_cutoff else None
    except ValueError:
        tx_cutoff = None
    if tx_cutoff is None:
        tx_cutoff = (market_now("US").date() - timedelta(days=90)).isoformat()

    all_orig = [tx for tx in txs if str(tx.get("form") or "").upper() != "4/A"]
    all_amend = [tx for tx in txs if str(tx.get("form") or "").upper() == "4/A"]
    has_amendments = bool(all_amend) or any(
        "amendment" in w.lower() or "double-count" in w.lower() for w in sec_warnings
    )

    ledger: list[dict] = [_f4_make_ledger_entry(tx, from_orig=True) for tx in all_orig if _f4_is_sell(tx)]
    non_sell_histories: list[list[dict]] = [[tx] for tx in all_orig if not _f4_is_sell(tx)]
    affected_owners: set[tuple] = set()
    unaligned_global = has_amendments and not all_amend

    def _mark_uncertain(tx: dict) -> None:
        nonlocal unaligned_global
        ok = _f4_owner_key(tx)
        if ok != ("",):
            affected_owners.add(ok)
        else:
            unaligned_global = True

    sorted_acc_items, order_uncertain_txs = _f4_sort_amendment_accessions(all_amend)
    for tx in order_uncertain_txs:
        _mark_uncertain(tx)

    for _acc, acc_rows in sorted_acc_items:
        intents, ambig_flags, row_texts, unresolved_conversions = _f4_resolve_accession_intents(
            acc_rows, ledger, non_sell_histories
        )
        acc_sells: list[tuple[dict, tuple[bool, bool], bool, str]] = []
        acc_non_sells: list[tuple[dict, tuple[bool, bool], bool, str, bool]] = []
        for i, (tx, intent, is_ambig, rtext) in enumerate(zip(acc_rows, intents, ambig_flags, row_texts)):
            if is_ambig:
                _mark_uncertain(tx)
            if _f4_is_sell(tx):
                acc_sells.append((tx, intent, is_ambig, rtext))
            else:
                acc_non_sells.append((tx, intent, is_ambig, rtext, i in unresolved_conversions))

        prior_len = len(ledger)
        acc_matched_ns, pending_reclassifications = _f4_reconcile_non_sells(
            ledger, non_sell_histories, acc_non_sells, prior_len, _mark_uncertain
        )
        _f4_reconcile_sells(ledger, acc_sells, prior_len, _mark_uncertain, excluded_entries=acc_matched_ns)
        for target, gtx in pending_reclassifications:
            _f4_mark_reclassified(ledger, target, gtx)

    active_sales = [entry for entry in ledger if not entry["reclassified"]]
    recent_active_sales: list[dict] = []
    has_undated_sale = False
    for entry in active_sales:
        td = _f4_tx_date_iso(entry["tx"])
        if td is None:
            has_undated_sale = True
        elif td >= tx_cutoff:
            recent_active_sales.append(entry)

    dedup_sales = len(recent_active_sales)
    unaffected_orig_sells = (
        0
        if unaligned_global
        else sum(
            1
            for entry in recent_active_sales
            if entry["from_orig"] and _f4_owner_key(entry["tx"]) not in affected_owners
        )
    )
    result["sales_90d"] = dedup_sales
    result["amendments_present"] = has_amendments
    result["count_uncertain"] = bool(has_undated_sale or affected_owners or unaligned_global)
    if has_undated_sale:
        sec_warnings.append(
            "SEC Form 4 sales with missing or invalid transaction dates were excluded from the 90-day count"
        )
    if affected_owners or unaligned_global:
        sec_warnings.append(
            "SEC Form 4/A amendments include unlinked or ambiguous revisions; insider sale count is uncertain"
        )
    # Only trigger high-severity buy veto when reconciled, unaffected original Form 4 sells reach threshold.
    if unaffected_orig_sells >= 6:
        result["flags"].append(
            {
                "category": "insider",
                "severity": "high",
                "description": f"近90日 SEC Form 4 记录到 {dedup_sales} 笔内部人卖出",
                "source": "data",
            }
        )
    elif dedup_sales >= 3:
        note = (
            "（含 Form 4/A 修订申报，未因未对齐/可能重复记录触发一票否决）"
            if has_amendments and dedup_sales >= 6
            else ""
        )
        result["flags"].append(
            {
                "category": "insider",
                "severity": "medium",
                "description": f"近90日 SEC Form 4 记录到 {dedup_sales} 笔内部人卖出{note}",
                "source": "data",
            }
        )
    return result


def _cat_cn(cat: str) -> str:
    return {"insider": "内部人减持", "earnings": "业绩预警", "regulatory": "监管处罚", "industry": "行业政策"}.get(
        cat, cat
    )


# --------------- Scoring ---------------


def compute_risk(flags: list) -> dict:
    score = 0
    for f in flags:
        sev = f.get("severity", "low")
        if sev == "high":
            score += 30
        elif sev == "medium":
            score += 15
        else:
            score += 5
    score = min(score, 100)

    if score >= 60:
        risk_level = "high"
    elif score >= 30:
        risk_level = "medium"
    else:
        risk_level = "low"

    veto_buy = any(f.get("severity") == "high" for f in flags)
    return {"risk_score": score, "risk_level": risk_level, "veto_buy": veto_buy}


# --------------- Main entry ---------------


def screen_risk(symbol: str, name: str = "", *, inputs=None, run=None) -> dict:
    market = detect_market(symbol)
    if market not in ("A", "HK", "US"):
        return {
            "symbol": symbol,
            "name": name,
            "market": market,
            "error": f"screen_risk only supports A, HK, and US markets (got {market})",
        }
    inputs = inputs or {}

    def supplied(key):
        value = inputs[key]
        return value.result() if isinstance(value, Future) else value

    with ThreadPoolExecutor(max_workers=4) as pool:
        valuation = pool.submit(
            lambda: check_valuation(symbol, quote=supplied("quote")) if "quote" in inputs else check_valuation(symbol)
        )
        technical = pool.submit(
            lambda: (
                check_technical(symbol, ta=supplied("technical")) if "technical" in inputs else check_technical(symbol)
            )
        )
        market_specific = None
        if market == "A":
            market_specific = pool.submit(check_lockup, symbol, **({"run": run} if run else {}))
        elif market == "HK":
            market_specific = pool.submit(check_hk_disclosures, symbol, **({"run": run} if run else {}))
        elif market == "US":
            market_specific = pool.submit(check_us_insider_trades, symbol, **({"run": run} if run else {}))
        searches = pool.submit(check_news_risks, symbol, name, market=market, **({"run": run} if run else {}))
        val = valuation.result()
        tech = technical.result()
        extra = market_specific.result() if market_specific is not None else None
        news = searches.result()
    all_flags = []
    checks = {}
    warnings = []

    checks["valuation"] = {k: val[k] for k in ("pe", "pb", "status", "available")}
    all_flags.extend(val["flags"])

    checks["technical"] = {
        "buy_signal": tech.get("buy_signal"),
        "signal_score": tech.get("signal_score"),
        "trend": tech.get("trend"),
        "available": tech["available"],
    }
    all_flags.extend(tech["flags"])

    if market == "A":
        checks["lockup"] = {"upcoming_30d": extra["upcoming_30d"], "available": extra["available"]}
        all_flags.extend(extra["flags"])
    else:
        checks["lockup"] = {"applicable": False, "upcoming_30d": [], "available": True}
        if market == "HK":
            checks["disclosures"] = {
                "matches": extra["matches"],
                "available": extra["available"],
                "truncated": extra.get("truncated", False),
            }
            all_flags.extend(extra["flags"])
            warnings.extend(extra.get("warnings") or [])
        elif market == "US":
            checks["insider_trades"] = {
                "sales_90d": extra["sales_90d"],
                "available": extra["available"],
                "configured": extra["configured"],
                "truncated": extra.get("truncated", False),
                "count_uncertain": extra.get("count_uncertain", False),
            }
            all_flags.extend(extra["flags"])
            if not extra["configured"]:
                warnings.append("SEC_USER_AGENT not set; SEC Form 4 insider trade risk check skipped")
            else:
                if extra.get("truncated"):
                    warnings.append("SEC Form 4 filings were truncated at top 10; insider sales coverage is incomplete")
                warnings.extend(extra.get("warnings") or [])

    checks["news_risks"] = {k: v for k, v in news.items() if k != "flags"}
    all_flags.extend(news["flags"])

    risk = compute_risk(all_flags)
    missing = [
        key
        for key, value in checks.items()
        if not value.get("available") and not (key == "insider_trades" and value.get("configured") is False)
    ]
    complete = not missing
    warnings.extend(f"{key} risk data unavailable or incomplete" for key in missing)
    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "risk_level": "unknown" if missing and risk["risk_level"] == "low" else risk["risk_level"],
        "risk_score": risk["risk_score"] if all_flags or complete else None,
        "veto_buy": risk["veto_buy"] if risk["veto_buy"] or complete else None,
        "data_complete": complete,
        "warnings": warnings,
        "flags": all_flags,
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(description="Risk screening tool")
    sub = parser.add_subparsers(dest="command")

    p_screen = sub.add_parser("screen")
    p_screen.add_argument("symbol")
    p_screen.add_argument("--name", default="")
    p_lockup = sub.add_parser("lockup", help="Internal bounded lockup query")
    p_lockup.add_argument("symbol")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "screen":
        result = screen_risk(args.symbol, args.name)
        print(json.dumps(result, ensure_ascii=False, default=str))
        if isinstance(result, dict) and "error" in result:
            sys.exit(1)
    elif args.command == "lockup":
        print(json.dumps(_fetch_lockup(args.symbol), ensure_ascii=False, default=str))


if __name__ == "__main__":
    utf8_stdio()
    main()
