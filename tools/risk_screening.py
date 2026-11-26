#!/usr/bin/env python3
"""Risk screening — 7-dimension risk checker for A-share stocks."""

import argparse
import json
import math
import os
import re
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, timedelta

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

    def _tx_code(tx: dict) -> str:
        return str(tx.get("code") or tx.get("transaction_code") or "").upper()

    def _is_sell(tx: dict) -> bool:
        return _tx_code(tx) == "S" or str(tx.get("action") or "").lower() == "sell"

    def _owner_key(tx: dict) -> tuple:
        owners = tx.get("owners")
        if isinstance(owners, list) and owners:
            return tuple(sorted(str(o.get("cik") or o.get("name") or "") for o in owners if isinstance(o, dict)))
        return (str(tx.get("owner") or tx.get("insider") or ""),)

    def _exact_sig(tx: dict) -> tuple:
        return (
            _owner_key(tx),
            str(tx.get("transaction_date") or tx.get("date") or ""),
            str(tx.get("security") or ""),
            bool(tx.get("derivative")),
            tx.get("shares"),
            tx.get("price"),
        )

    def _base_sig(tx: dict) -> tuple:
        return (
            _owner_key(tx),
            str(tx.get("transaction_date") or tx.get("date") or ""),
            str(tx.get("security") or ""),
            bool(tx.get("derivative")),
        )

    _ADDITION_RE = re.compile(
        r"(omit(?:ted|ting)?|inadvertently\s+omitted|previously\s+unreported|"
        r"add(?:ed|ing|itional)\s+(?:a\s+)?(?:previously\s+omitted\s+)?transactions?|"
        r"late[- ]?report(?:ed|ing)|补报|漏报|遗漏)",
        re.IGNORECASE,
    )
    _CORRECTION_RE = re.compile(
        r"(correct(?:ed|ing|ion|s)?|reclassif(?:ied|y|ying)|instead\s+of|"
        r"erroneously\s+reported|incorrectly\s+reported|misreported|should\s+have\s+been|"
        r"replaces?|supersedes?|restat(?:ed|es|ing|ement)|更正|修正|重分类|误报)",
        re.IGNORECASE,
    )
    _SALE_RECLASS_RE = re.compile(
        r"(\b(?:sale|sold|sell|code\s+s|transaction\s+code|reclassif(?:ied|y|ying)|instead\s+of|should\s+have\s+been)\b|"
        r"\bto\s+(?:a\s+)?(?:bona\s+fide\s+)?gift\b|卖出|出售|重分类|改为赠与)",
        re.IGNORECASE,
    )
    _COUNT_WORDS = {
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "both": 2,
        "several": 2,
        "multiple": 2,
        "两": 2,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "多": 2,
        "若干": 2,
    }
    _ADD_COUNT_RE = re.compile(
        r"(?:\b([2-9]|\d{2,}|two|three|four|five|six|seven|eight|nine|ten|both|several|multiple)\b"
        r"(?:\s+\w+){0,3}\s+(?:omitted|unreported|additional|late[- ]?reported)\b|"
        r"(?:adds?|adding|added|reports?|reporting|reported)\s+"
        r"\b([2-9]|\d{2,}|two|three|four|five|six|seven|eight|nine|ten|both|several|multiple)\b|"
        r"(两|二|三|四|五|多|若干)\s*笔?\s*(?:遗漏|漏报|补报))",
        re.IGNORECASE,
    )
    _ADD_PLURAL_RE = re.compile(
        r"(?:omitted|unreported|additional|late[- ]?reported)\s+(?:sales|transactions|dispositions|lines|rows)\b",
        re.IGNORECASE,
    )
    _CORR_COUNT_RE = re.compile(
        r"(?:corrects?|correcting|corrected|reclassif(?:ies|ying|ied)|restates?|restating|restated)\s+"
        r"\b([2-9]|\d{2,}|two|three|four|five|six|seven|eight|nine|ten|both|several|multiple)\b|"
        r"(两|二|三|四|五|多|若干)\s*笔?\s*(?:更正|修正|重分类|误报)",
        re.IGNORECASE,
    )

    def _remarks_intent_count(text: str, *, kind: str) -> int:
        if kind == "add":
            if not _ADDITION_RE.search(text):
                return 0
            count = 1
            for m in _ADD_COUNT_RE.finditer(text):
                tok = next((g for g in m.groups() if g), "").lower()
                n = int(tok) if tok.isdigit() else _COUNT_WORDS.get(tok, 1)
                count = max(count, n)
            if count == 1 and _ADD_PLURAL_RE.search(text):
                count = 2
            return count
        if not _CORRECTION_RE.search(text):
            return 0
        count = 1
        for m in _CORR_COUNT_RE.finditer(text):
            tok = next((g for g in m.groups() if g), "").lower()
            n = int(tok) if tok.isdigit() else _COUNT_WORDS.get(tok, 1)
            count = max(count, n)
        return count

    def _extract_ref_ids(refs) -> list[str]:
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

    def _row_scoped_text(tx: dict) -> str:
        footnotes = tx.get("footnotes")
        ref_ids = _extract_ref_ids(tx.get("footnote_refs"))
        row_parts: list[str] = []
        if isinstance(footnotes, dict) and footnotes and ref_ids:
            row_parts.extend(str(footnotes[fid]) for fid in ref_ids if footnotes.get(fid))
        elif isinstance(footnotes, str) and footnotes:
            row_parts.append(footnotes)
        row_parts.extend(str(tx[k]) for k in ("amendment_reason", "note") if tx.get(k))
        return " ".join(row_parts)

    def _resolve_accession_intents(
        acc_rows: list[dict],
    ) -> tuple[list[tuple[bool, bool]], list[bool], list[str], bool]:
        row_texts = [_row_scoped_text(tx) for tx in acc_rows]
        row_intents = [
            (bool(_ADDITION_RE.search(rt)), bool(_CORRECTION_RE.search(rt))) if rt else (False, False)
            for rt in row_texts
        ]
        ambiguous = [False] * len(acc_rows)
        seen_remarks: set[str] = set()
        remarks_parts: list[str] = []
        for tx in acc_rows:
            rem = str(tx.get("remarks") or "").strip()
            if rem and rem not in seen_remarks:
                seen_remarks.add(rem)
                remarks_parts.append(rem)
        if not remarks_parts:
            return row_intents, ambiguous, row_texts, False
        rem_text = " ".join(remarks_parts)
        rem_add_count = _remarks_intent_count(rem_text, kind="add")
        rem_corr_count = _remarks_intent_count(rem_text, kind="corr")
        if not (rem_add_count or rem_corr_count):
            return row_intents, ambiguous, row_texts, False
        claimed_add_count = sum(1 for ra, _ in row_intents if ra)
        claimed_corr_count = sum(1 for _, rc in row_intents if rc)
        unclaimed_add = rem_add_count > claimed_add_count
        unclaimed_corr = rem_corr_count > claimed_corr_count
        if not (unclaimed_add or unclaimed_corr):
            return row_intents, ambiguous, row_texts, False
        unattributed = [i for i, (ra, rc) in enumerate(row_intents) if not (ra or rc)]
        if len(unattributed) == 1 and (unclaimed_add != unclaimed_corr):
            row_intents[unattributed[0]] = (unclaimed_add, unclaimed_corr)
            row_texts[unattributed[0]] = f"{row_texts[unattributed[0]]} {rem_text}".strip()
            return row_intents, ambiguous, row_texts, False
        # Filing-level remarks cannot be uniquely attributed (multiple unattributed rows, mixed intent, or
        # remarks declaring more additions/corrections than attributed rows); surface uncertainty instead of
        # broadcasting to every row.
        for i in unattributed:
            ambiguous[i] = True
            row_texts[i] = f"{row_texts[i]} {rem_text}".strip()
        if not unattributed:
            for i in range(len(acc_rows)):
                ambiguous[i] = True
        return row_intents, ambiguous, row_texts, unclaimed_add

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

    def _make_ledger_entry(tx: dict, *, from_orig: bool) -> dict:
        shares = tx.get("shares")
        fdate = str(tx.get("filing_date") or "").strip()
        return {
            "tx": tx,
            "orig_tx": tx,
            "sigs": {_exact_sig(tx)},
            "shares_set": {shares} if shares is not None else set(),
            "filing_dates": {fdate} if fdate else set(),
            "orig_accession": str(tx.get("accession") or "").strip() or None,
            "from_orig": from_orig,
            "reclassified": False,
        }

    # Unified chronological transaction ledger initialized from original Form 4 filings.
    ledger: list[dict] = [_make_ledger_entry(tx, from_orig=True) for tx in all_orig if _is_sell(tx)]
    known_non_sells: list[dict] = [tx for tx in all_orig if not _is_sell(tx)]
    affected_owners: set[tuple] = set()
    unaligned_global = has_amendments and not all_amend

    def _mark_uncertain(tx: dict) -> None:
        nonlocal unaligned_global
        ok = _owner_key(tx)
        if ok != ("",):
            affected_owners.add(ok)
        else:
            unaligned_global = True

    def _scoped_pool(tx: dict, prior_len: int) -> tuple[list[int], bool]:
        orig_date = str(tx.get("original_filing_date") or "").strip()
        if not orig_date or not any(ledger[i]["filing_dates"] for i in range(prior_len)):
            return list(range(prior_len)), False
        pool = [i for i in range(prior_len) if orig_date in ledger[i]["filing_dates"]]
        if not pool:
            return [], True
        b = _base_sig(tx)
        same_base_accs = {
            ledger[i]["orig_accession"] for i in pool if _base_sig(ledger[i]["tx"]) == b and ledger[i]["orig_accession"]
        }
        if len(same_base_accs) > 1:
            return [], True
        return pool, False

    def _mark_reclassified(idx: int, tx: dict) -> None:
        entry = ledger[idx]
        entry["reclassified"] = True
        fdate = str(tx.get("filing_date") or "").strip()
        if fdate:
            entry["filing_dates"].add(fdate)

    def _update_entry(idx: int, tx: dict, *, restore_if_reclassified: bool = False) -> None:
        entry = ledger[idx]
        entry["tx"] = tx
        entry["sigs"].add(_exact_sig(tx))
        if tx.get("shares") is not None:
            entry["shares_set"].add(tx.get("shares"))
        fdate = str(tx.get("filing_date") or "").strip()
        if fdate:
            entry["filing_dates"].add(fdate)
        if restore_if_reclassified and entry["reclassified"]:
            entry["reclassified"] = False

    # Group ALL Form 4/A rows (sells and non-sells together) by accession and process chronologically so later
    # amendments can correct, reclassify, or restore sales from original Form 4 or earlier Form 4/A filings.
    amend_by_acc: dict[str, list[dict]] = {}
    for tx in all_amend:
        acc = str(tx.get("accession") or f"__amend_{id(tx)}")
        amend_by_acc.setdefault(acc, []).append(tx)

    sorted_acc_items = sorted(
        amend_by_acc.items(),
        key=lambda item: (min(str(r.get("filing_date") or "") for r in item[1]), item[0]),
    )

    for _acc, acc_rows in sorted_acc_items:
        intents, ambig_flags, row_texts, acc_has_unclaimed_add = _resolve_accession_intents(acc_rows)
        acc_sells: list[tuple[dict, tuple[bool, bool], bool, str]] = []
        acc_non_sells: list[tuple[dict, tuple[bool, bool], bool, str]] = []
        for tx, intent, is_ambig, rtext in zip(acc_rows, intents, ambig_flags, row_texts):
            if is_ambig:
                _mark_uncertain(tx)
            if _is_sell(tx):
                acc_sells.append((tx, intent, is_ambig, rtext))
            else:
                acc_non_sells.append((tx, intent, is_ambig, rtext))

        # Match against the ledger snapshot as of BEFORE this accession's own newly added sales.
        prior_len = len(ledger)

        # 1. Reconcile this accession's non-sell 4/A rows against known non-sells and prior ledger entries
        #    (original Form 4 sells + sells added by earlier Form 4/A accessions) in order-independent passes.
        acc_orig_ns_matched: set[int] = set()
        acc_matched_ns: set[int] = set()
        acc_added_non_sells: list[dict] = []
        ns_candidates: list[tuple[dict, bool, bool]] = []
        for tx, (is_add, is_corr), _is_ambig, rtext in acc_non_sells:
            base = _base_sig(tx)
            code = _tx_code(tx)
            shares = tx.get("shares")
            orig_date = str(tx.get("original_filing_date") or "").strip()
            has_identity = base[0] != ("",) and bool(base[1]) and shares is not None
            pool, pool_ambig = _scoped_pool(tx, prior_len)
            ns_idx = next(
                (
                    i
                    for i, orig_ns in enumerate(known_non_sells)
                    if i not in acc_orig_ns_matched
                    and _base_sig(orig_ns) == base
                    and _tx_code(orig_ns) == code
                    and (shares is None or orig_ns.get("shares") == shares)
                    and (not orig_date or not orig_ns.get("filing_date") or orig_ns.get("filing_date") == orig_date)
                ),
                None,
            )
            if ns_idx is not None:
                # Resolve explicit sale reclassifications (or priced exact matches to a prior sale whose price differs
                # from the existing non-sell) against the ledger before applying the non-sell restatement shortcut.
                priced_sell_match = (
                    not pool_ambig
                    and has_identity
                    and tx.get("price") is not None
                    and known_non_sells[ns_idx].get("price") != tx.get("price")
                    and any(i not in acc_matched_ns and _exact_sig(tx) in ledger[i]["sigs"] for i in pool)
                )
                has_ledger_sell = not pool_ambig and any(
                    i not in acc_matched_ns and _base_sig(ledger[i]["tx"]) == base for i in pool
                )
                has_active_ledger_sell = not pool_ambig and any(
                    i not in acc_matched_ns and not ledger[i]["reclassified"] and _base_sig(ledger[i]["tx"]) == base
                    for i in pool
                )
                if priced_sell_match or (is_corr and has_ledger_sell and bool(_SALE_RECLASS_RE.search(rtext))):
                    ns_candidates.append((tx, is_add, is_corr))
                    continue
                if is_corr and has_active_ledger_sell:
                    acc_orig_ns_matched.add(ns_idx)
                    _mark_uncertain(tx)
                    continue
                acc_orig_ns_matched.add(ns_idx)
                continue
            if is_add and not is_corr:
                acc_added_non_sells.append(tx)
                continue
            if pool_ambig:
                _mark_uncertain(tx)
                continue
            ns_candidates.append((tx, is_add, is_corr))

        # Pass A1: exact (base, shares, price) matches first (independent of row order within accession).
        after_ns_a1: list[tuple[dict, bool, bool]] = []
        for tx, is_add, is_corr in ns_candidates:
            base = _base_sig(tx)
            shares = tx.get("shares")
            has_identity = base[0] != ("",) and bool(base[1]) and shares is not None
            pool, pool_ambig = _scoped_pool(tx, prior_len)
            if pool_ambig:
                _mark_uncertain(tx)
                continue
            if has_identity and tx.get("price") is not None:
                exact = _exact_sig(tx)
                exact_cands = [i for i in pool if i not in acc_matched_ns and exact in ledger[i]["sigs"]]
                if exact_cands:
                    # Prefer already-reclassified entry for idempotence across successive gift 4/A filings.
                    reclass_exact = [i for i in exact_cands if ledger[i]["reclassified"]]
                    target = reclass_exact[0] if reclass_exact else exact_cands[0]
                    acc_matched_ns.add(target)
                    _mark_reclassified(target, tx)
                    continue
            after_ns_a1.append((tx, is_add, is_corr))

        # Pass A2: unique (base, shares) matches (or uniform-price candidates) after exact matches are reserved.
        deferred_ns: list[tuple[dict, bool, bool]] = []
        for tx, is_add, is_corr in after_ns_a1:
            base = _base_sig(tx)
            shares = tx.get("shares")
            has_identity = base[0] != ("",) and bool(base[1]) and shares is not None
            pool, _ = _scoped_pool(tx, prior_len)
            if has_identity:
                share_cands = [
                    i
                    for i in pool
                    if i not in acc_matched_ns
                    and _base_sig(ledger[i]["tx"]) == base
                    and shares in ledger[i]["shares_set"]
                ]
                if len(share_cands) == 1 or (
                    len(share_cands) > 1 and len({ledger[i]["tx"].get("price") for i in share_cands}) == 1
                ):
                    reclass_cands = [i for i in share_cands if ledger[i]["reclassified"]]
                    target = reclass_cands[0] if reclass_cands else share_cands[0]
                    acc_matched_ns.add(target)
                    _mark_reclassified(target, tx)
                    continue
            deferred_ns.append((tx, is_add, is_corr))

        # Pass B: handle remaining multi-candidate blank-price links and explicit share-changing corrections.
        for tx, _is_add, is_corr in deferred_ns:
            base = _base_sig(tx)
            shares = tx.get("shares")
            has_identity = base[0] != ("",) and bool(base[1]) and shares is not None
            pool, _ = _scoped_pool(tx, prior_len)
            if has_identity:
                share_cands = [
                    i
                    for i in pool
                    if i not in acc_matched_ns
                    and _base_sig(ledger[i]["tx"]) == base
                    and shares in ledger[i]["shares_set"]
                ]
                if len(share_cands) == 1:
                    acc_matched_ns.add(share_cands[0])
                    _mark_reclassified(share_cands[0], tx)
                    continue
                if len(share_cands) > 1:
                    already_reclass = [i for i in share_cands if ledger[i]["reclassified"]]
                    if len(already_reclass) == 1:
                        acc_matched_ns.add(already_reclass[0])
                        continue
                    _mark_uncertain(tx)
                    continue
            if is_corr and base[0] != ("",) and bool(base[1]):
                base_cands = [i for i in pool if i not in acc_matched_ns and _base_sig(ledger[i]["tx"]) == base]
                if len(base_cands) == 1:
                    acc_matched_ns.add(base_cands[0])
                    _mark_reclassified(base_cands[0], tx)
                    continue
                already_reclass = [i for i in base_cands if ledger[i]["reclassified"]]
                if len(already_reclass) == 1:
                    acc_matched_ns.add(already_reclass[0])
                    continue
                _mark_uncertain(tx)
                continue
            if not has_identity:
                _mark_uncertain(tx)

        known_non_sells.extend(acc_added_non_sells)

        # 2. Match this accession's sell 4/A rows against all prior ledger entries (reserving each entry by row
        #    identity across match types, exact matches first, independent of XML row order).
        acc_matched_sell: set[int] = set()
        acc_explicit_adds: list[dict] = []
        sell_candidates: list[tuple[dict, bool]] = []
        for tx, (is_add, is_corr), _is_ambig, _rtext in acc_sells:
            if is_add and not is_corr:
                acc_explicit_adds.append(tx)
            else:
                sell_candidates.append((tx, is_corr))

        # Sub-pass 1: exact matches against prior ledger entries first.
        after_exact: list[tuple[dict, bool]] = []
        for tx, is_corr in sell_candidates:
            pool, pool_ambig = _scoped_pool(tx, prior_len)
            if pool_ambig:
                _mark_uncertain(tx)
                continue
            exact = _exact_sig(tx)
            exact_cands = [i for i in pool if i not in acc_matched_sell and exact in ledger[i]["sigs"]]
            if exact_cands:
                if is_corr:
                    reclass_cands = [i for i in exact_cands if ledger[i]["reclassified"]]
                    if len(reclass_cands) == 1 or (
                        len(reclass_cands) > 1
                        and len({(ledger[i]["tx"].get("price"), ledger[i]["tx"].get("shares")) for i in reclass_cands})
                        == 1
                    ):
                        target = reclass_cands[0]
                        acc_matched_sell.add(target)
                        _update_entry(target, tx, restore_if_reclassified=True)
                    elif reclass_cands:
                        _mark_uncertain(tx)
                    else:
                        target = exact_cands[0]
                        acc_matched_sell.add(target)
                        _update_entry(target, tx)
                else:
                    active_cands = [i for i in exact_cands if not ledger[i]["reclassified"]]
                    if active_cands:
                        acc_matched_sell.add(active_cands[0])
                        _update_entry(active_cands[0], tx)
                    else:
                        # Unannotated restatement of a previously reclassified sale: keep gift state & mark uncertain.
                        acc_matched_sell.add(exact_cands[0])
                        _mark_uncertain(tx)
            else:
                after_exact.append((tx, is_corr))

        # Sub-pass 2: share-matched restatements against remaining prior ledger entries (order-independent:
        # multiple distinct-price or mixed-state candidates are left unresolved and marked uncertain).
        after_share: list[tuple[dict, bool]] = []
        for tx, is_corr in after_exact:
            b = _base_sig(tx)
            shares = tx.get("shares")
            pool, _ = _scoped_pool(tx, prior_len)
            share_cands: list[int] = []
            if shares is not None and b[0] != ("",) and bool(b[1]):
                share_cands = [
                    i
                    for i in pool
                    if i not in acc_matched_sell
                    and _base_sig(ledger[i]["tx"]) == b
                    and shares in ledger[i]["shares_set"]
                ]
            if len(share_cands) == 1 or (
                len(share_cands) > 1
                and len({(ledger[i]["tx"].get("price"), ledger[i]["reclassified"]) for i in share_cands}) == 1
            ):
                target = share_cands[0]
                acc_matched_sell.add(target)
                was_reclassified = ledger[target]["reclassified"]
                _update_entry(target, tx, restore_if_reclassified=is_corr)
                if not (is_corr and was_reclassified):
                    _mark_uncertain(tx)
            elif len(share_cands) > 1:
                _mark_uncertain(tx)
            else:
                after_share.append((tx, is_corr))

        # Sub-pass 3: explicit corrections against remaining prior ledger entries (order-independent: resolve only
        # when a unique or symmetric candidate exists; otherwise mark uncertain without inflating count).
        acc_new_sales: list[dict] = list(acc_explicit_adds)
        for tx, is_corr in after_share:
            b = _base_sig(tx)
            pool, _ = _scoped_pool(tx, prior_len)
            if is_corr:
                if b[0] != ("",) and bool(b[1]):
                    base_cands = [i for i in pool if i not in acc_matched_sell and _base_sig(ledger[i]["tx"]) == b]
                    if len(base_cands) == 1 or (
                        len(base_cands) > 1
                        and len(
                            {
                                (ledger[i]["tx"].get("shares"), ledger[i]["tx"].get("price"), ledger[i]["reclassified"])
                                for i in base_cands
                            }
                        )
                        == 1
                    ):
                        target = base_cands[0]
                        acc_matched_sell.add(target)
                        _update_entry(target, tx, restore_if_reclassified=True)
                        _mark_uncertain(tx)
                        continue
                _mark_uncertain(tx)
                continue
            # Sub-pass 4: only confirm an unlinked sell row as a new sale when filing evidence supports an addition
            # (unclaimed remarks addition, or every prior entry on that base signature was already reserved in this
            # same accession). Otherwise report uncertainty without inflating the confirmed sale count.
            has_prior_same_base = any(_base_sig(ledger[i]["tx"]) == b for i in pool)
            all_prior_same_base_reserved = has_prior_same_base and all(
                i in acc_matched_sell for i in pool if _base_sig(ledger[i]["tx"]) == b
            )
            if acc_has_unclaimed_add or all_prior_same_base_reserved:
                acc_new_sales.append(tx)
            else:
                _mark_uncertain(tx)

        # Merge newly disclosed sales from this accession idempotently against existing 4/A-added ledger entries.
        acc_new_counts: dict[tuple, list[dict]] = {}
        for tx in acc_new_sales:
            acc_new_counts.setdefault(_exact_sig(tx), []).append(tx)
        existing_counts: dict[tuple, int] = {}
        for entry in ledger:
            if not entry["from_orig"]:
                for sig in entry["sigs"]:
                    existing_counts[sig] = existing_counts.get(sig, 0) + 1
        for e, rows in acc_new_counts.items():
            need = len(rows) - existing_counts.get(e, 0)
            if need > 0:
                ledger.extend(_make_ledger_entry(tx, from_orig=False) for tx in rows[:need])

    def _tx_date_iso(tx: dict) -> str | None:
        raw = str(tx.get("transaction_date") or tx.get("date") or "").strip()[:10]
        if not raw:
            return None
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return None

    active_sales = [entry for entry in ledger if not entry["reclassified"]]
    recent_active_sales: list[tuple[dict, bool]] = []
    for entry in active_sales:
        td = _tx_date_iso(entry["tx"])
        if td is None:
            recent_active_sales.append((entry, False))
        elif td >= tx_cutoff:
            recent_active_sales.append((entry, True))

    dedup_sales = len(recent_active_sales)
    unaffected_orig_sells = (
        0
        if unaligned_global
        else sum(
            1
            for entry, has_valid_date in recent_active_sales
            if has_valid_date and entry["from_orig"] and _owner_key(entry["tx"]) not in affected_owners
        )
    )
    result["sales_90d"] = dedup_sales
    result["amendments_present"] = has_amendments
    result["count_uncertain"] = bool(affected_owners or unaligned_global)
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
                if extra.get("count_uncertain"):
                    warnings.append(
                        "SEC Form 4/A amendments include unlinked or ambiguous revisions; insider sale count is uncertain"
                    )
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
