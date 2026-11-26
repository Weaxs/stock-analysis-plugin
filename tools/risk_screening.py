#!/usr/bin/env python3
"""Risk screening — 7-dimension risk checker for A-share stocks."""

import argparse
import json
import math
import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta

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


_HK_DISCLOSURE_HIGH = ("清盘", "winding up", "停牌", "trading halt", "suspension", "监管调查", "investigation")
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


def check_hk_disclosures(symbol: str, *, run=None) -> dict:
    data = (run or _run_tool)(
        "value_data.py", ["announcements", symbol, "--category", "all", "--top", "15"], timeout=25
    )
    result = {"matches": [], "flags": [], "available": False}
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get("announcements"), list):
        return result
    result["available"] = True
    for item in data["announcements"]:
        title = str(item.get("title") or "")
        low = title.lower()
        sev = None
        if any(k in low or k in title for k in _HK_DISCLOSURE_HIGH):
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
    result["available"] = not truncated and not sec_warnings
    txs = [tx for tx in data["transactions"] if isinstance(tx, dict)]

    def _is_sell(tx: dict) -> bool:
        return (
            str(tx.get("code") or tx.get("transaction_code") or "").upper() == "S"
            or str(tx.get("action") or "").lower() == "sell"
        )

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

    all_orig = [tx for tx in txs if str(tx.get("form") or "").upper() != "4/A"]
    all_amend = [tx for tx in txs if str(tx.get("form") or "").upper() == "4/A"]
    has_amendments = bool(all_amend) or any(
        "amendment" in w.lower() or "double-count" in w.lower() for w in sec_warnings
    )

    # Start with original Form 4 sells, then reconcile Form 4/A amendments before counting.
    active_orig_sells = [tx for tx in all_orig if _is_sell(tx)]
    reclassified_indices: set[int] = set()
    affected_owners: set[tuple] = set()
    unaligned_global = has_amendments and not all_amend

    # 1. Reconcile non-sell 4/A rows (e.g., an S transaction corrected to Gift G): remove superseded original sells.
    for tx in all_amend:
        if _is_sell(tx):
            continue
        exact = _exact_sig(tx)
        base = _base_sig(tx)
        matched_idx = next(
            (
                i
                for i, orig in enumerate(active_orig_sells)
                if i not in reclassified_indices and _exact_sig(orig) == exact
            ),
            None,
        )
        if matched_idx is None:
            matched_idx = next(
                (
                    i
                    for i, orig in enumerate(active_orig_sells)
                    if i not in reclassified_indices
                    and _base_sig(orig) == base
                    and tx.get("shares") is not None
                    and orig.get("shares") == tx.get("shares")
                ),
                None,
            )
        if matched_idx is None and base[1]:
            matched_idx = next(
                (
                    i
                    for i, orig in enumerate(active_orig_sells)
                    if i not in reclassified_indices and _base_sig(orig) == base
                ),
                None,
            )
        if matched_idx is not None:
            reclassified_indices.add(matched_idx)
        else:
            ok = _owner_key(tx)
            if ok != ("",):
                affected_owners.add(ok)
            else:
                unaligned_global = True

    confirmed_orig_sells = [tx for i, tx in enumerate(active_orig_sells) if i not in reclassified_indices]

    # 2. Deduplicate Form 4/A sell rows against confirmed original sells; flag unaligned 4/A sells.
    seen_counts: dict[tuple, int] = {}
    for tx in confirmed_orig_sells:
        sig = _exact_sig(tx)
        seen_counts[sig] = seen_counts.get(sig, 0) + 1
    unique_amended_sells = 0
    amended_by_acc: dict[str, list[dict]] = {}
    for tx in all_amend:
        if not _is_sell(tx):
            continue
        acc = str(tx.get("accession") or f"__amend_{id(tx)}")
        amended_by_acc.setdefault(acc, []).append(tx)
    for acc_rows in amended_by_acc.values():
        acc_sig_counts: dict[tuple, int] = {}
        acc_sample: dict[tuple, dict] = {}
        for tx in acc_rows:
            sig = _exact_sig(tx)
            acc_sig_counts[sig] = acc_sig_counts.get(sig, 0) + 1
            acc_sample[sig] = tx
        for sig, cnt in acc_sig_counts.items():
            already = seen_counts.get(sig, 0)
            if cnt > already:
                unique_amended_sells += cnt - already
                seen_counts[sig] = cnt
                ok = _owner_key(acc_sample[sig])
                if ok != ("",):
                    affected_owners.add(ok)
                else:
                    unaligned_global = True

    dedup_sales = len(confirmed_orig_sells) + unique_amended_sells
    unaffected_orig_sells = (
        0 if unaligned_global else sum(1 for tx in confirmed_orig_sells if _owner_key(tx) not in affected_owners)
    )
    result["sales_90d"] = dedup_sales
    result["amendments_present"] = has_amendments
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
            checks["disclosures"] = {"matches": extra["matches"], "available": extra["available"]}
            all_flags.extend(extra["flags"])
        elif market == "US":
            checks["insider_trades"] = {
                "sales_90d": extra["sales_90d"],
                "available": extra["available"],
                "configured": extra["configured"],
                "truncated": extra.get("truncated", False),
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
