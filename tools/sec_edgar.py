#!/usr/bin/env python3
"""SEC institutional 13F and issuer Form 4 disclosures, never realtime holdings."""

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cache import DAY, WEEK, cached
from _subproc import utf8_stdio

_last_request = 0.0
SEC_MIN_INTERVAL = 0.5


def identity():
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if "\n" in agent or "\r" in agent or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", agent):
        raise ValueError("Set SEC_USER_AGENT to your application name and real contact email")
    return agent


def sec_get(url, as_json=True, stream=False):
    global _last_request
    agent = identity()
    time.sleep(max(0, SEC_MIN_INTERVAL - (time.monotonic() - _last_request)))
    _last_request = time.monotonic()
    try:
        response = requests.get(
            url,
            headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"},
            timeout=(10, 30) if stream else 20,
            stream=stream,
        )
        response.raise_for_status()
        if stream:
            return response
        return response.json() if as_json else response.text
    except Exception as exc:
        raise ValueError(
            f"SEC data unavailable ({type(exc).__name__}); check identity/access and retry later"
        ) from None


def resolve_cik(symbol):
    if re.fullmatch(r"\d{1,10}", symbol) and int(symbol) > 0:
        return symbol.zfill(10)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,14}", symbol):
        raise ValueError("A US ticker or numeric CIK is required")
    mapping = cached("sec-tickers:US", WEEK, lambda: sec_get("https://www.sec.gov/files/company_tickers.json"))
    for row in mapping.values():
        if row["ticker"].upper().replace(".", "-") == symbol.upper().replace(".", "-"):
            return str(row["cik_str"]).zfill(10)
    raise ValueError("Ticker not found in SEC directory; supply issuer CIK")


def filing_rows(data):
    return [
        {key: values[i] for key, values in data.items() if isinstance(values, list) and i < len(values)}
        for i in range(len(data.get("accessionNumber", [])))
    ]


def submissions(cik, forms, cutoff=None):
    data = cached(f"sec-submissions:{cik}", DAY, lambda: sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    if not isinstance(data.get("filings", {}).get("recent"), dict):
        raise ValueError("SEC submissions metadata unavailable")
    rows = filing_rows(data.get("filings", {}).get("recent", {}))
    files = data.get("filings", {}).get("files", [])
    relevant = [f for f in files if not cutoff or f.get("filingTo", "") >= cutoff]
    relevant.sort(key=lambda f: f.get("filingTo", ""), reverse=True)
    loaded = 0
    # shortcut: at most three older submission shards; expose incomplete coverage when more are needed.
    for file in relevant[:3]:
        latest = max((r.get("reportDate") or "" for r in rows if r.get("form") in forms), default="")
        if (
            not cutoff
            and file.get("filingTo", "") <= latest
            and any(r.get("form") == "13F-HR" and (r.get("reportDate") or "") == latest for r in rows)
        ):
            break
        name = file.get("name", "")
        if not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
            raise ValueError("Unexpected SEC submissions filename")
        extra = cached(
            f"sec-submission-file:{name}", DAY, lambda name=name: sec_get(f"https://data.sec.gov/submissions/{name}")
        )
        rows.extend(filing_rows(extra))
        loaded += 1
    result = [r for r in rows if r.get("form") in forms and (not cutoff or r.get("filingDate", "") >= cutoff)]
    result.sort(key=lambda r: (r.get("filingDate", ""), r.get("accessionNumber", "")), reverse=True)
    earliest_unsearched = cutoff or max((r.get("reportDate") or "" for r in result), default="")
    incomplete = any(f.get("filingTo", "") >= earliest_unsearched for f in relevant[loaded:])
    return data.get("name"), result, incomplete


def archive_base(cik, filing):
    accession = filing.get("accessionNumber", "")
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise ValueError("Invalid SEC accession number")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"


def xml_root(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Unexpected XML entity declaration")
    root = ET.fromstring(text)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def footnote_refs(node, path=""):
    refs = {}
    for child in node:
        child_path = f"{path}/{child.tag}" if path else child.tag
        if child.tag == "footnoteId":
            refs.setdefault(path, []).append(child.get("id"))
        else:
            refs.update(footnote_refs(child, child_path))
    return refs


def numeric(node, path):
    value = node.findtext(path)
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def holdings(args):
    identity()
    if not re.fullmatch(r"\d{1,10}", args.cik) or int(args.cik) == 0:
        raise ValueError("13F requires a manager or issuer CIK (1..10 digits)")
    cik = args.cik.zfill(10)
    mode = getattr(args, "mode", "auto")
    if mode not in ("auto", "manager", "issuer"):
        raise ValueError("mode must be auto, manager or issuer")
    if mode != "manager":
        data = cached(f"sec-submissions:{cik}", DAY, lambda: sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
        if not isinstance(data.get("filings", {}).get("recent"), dict):
            raise ValueError("SEC submissions metadata unavailable")
        forms = data["filings"]["recent"].get("form", [])
        is_manager = any(form in ("13F-HR", "13F-HR/A") for form in forms)
        if mode == "auto" and not is_manager:
            _, archived, _ = submissions(cik, {"13F-HR", "13F-HR/A"})
            is_manager = any(row["form"] == "13F-HR" for row in archived)
        if mode == "issuer" or not is_manager:
            from sec_holders import issuer_holders

            return issuer_holders(cik, data)
    name, filings, incomplete = submissions(cik, {"13F-HR", "13F-HR/A"})
    originals = [r for r in filings if r["form"] == "13F-HR"]
    if not originals:
        raise ValueError("No original 13F-HR in available submission history; issuer CIK is not a holder index")
    filing = max(originals, key=lambda r: (r.get("reportDate", ""), r.get("filingDate", "")))
    if max((r.get("reportDate") or "" for r in filings), default="") > (filing.get("reportDate") or ""):
        raise ValueError("Latest 13F report period has amendments but its original is unavailable in searched history")
    amendments = [r for r in filings if r["form"] == "13F-HR/A" and r.get("reportDate") == filing.get("reportDate")]
    base = archive_base(cik, filing)

    def fetch_table():
        listing = sec_get(f"{base}/index.json")["directory"]["item"]
        candidates = [r["name"] for r in listing if re.fullmatch(r"[\w.-]+\.xml", r.get("name", ""), re.I)]
        for filename in candidates[:10]:
            try:
                root = xml_root(sec_get(f"{base}/{filename}", as_json=False))
            except ET.ParseError:
                continue
            if root.tag == "informationTable":
                return [
                    {
                        "issuer": row.findtext("nameOfIssuer"),
                        "class": row.findtext("titleOfClass"),
                        "cusip": row.findtext("cusip"),
                        "value": numeric(row, "value"),
                        "shares": numeric(row, "shrsOrPrnAmt/sshPrnamt"),
                        "share_type": row.findtext("shrsOrPrnAmt/sshPrnamtType"),
                        "put_call": row.findtext("putCall"),
                        "investment_discretion": row.findtext("investmentDiscretion"),
                    }
                    for row in root.findall("infoTable")
                ]
        raise ValueError("13F information table XML unavailable")

    rows = cached(f"sec-13f:{cik}:{filing['accessionNumber']}", WEEK, fetch_table)
    result = {
        "cik": cik,
        "manager": name,
        "query_type": "manager",
        "source": "sec_edgar",
        "report_date": filing.get("reportDate"),
        "filing_date": filing["filingDate"],
        "accession": filing["accessionNumber"],
        "filing_url": f"{base}/{filing['accessionNumber']}-index.html",
        "holdings": rows,
        "value_unit": "USD" if filing["filingDate"] >= "2023-01-03" else "USD thousands",
        "disclosure_lag_days": (
            date.fromisoformat(filing["filingDate"]) - date.fromisoformat(filing["reportDate"])
        ).days,
        "filing_deadline_days": 45,
        "data_age_days": (date.today() - date.fromisoformat(filing["reportDate"])).days,
        "realtime": False,
        "history_truncated": incomplete,
        "coverage": "13F reportable securities only; not a complete portfolio or issuer-to-managers index",
        "amendments": [
            {
                "accession": r["accessionNumber"],
                "filing_date": r["filingDate"],
                "filing_url": f"{archive_base(cik, r)}/{r['accessionNumber']}-index.html",
            }
            for r in amendments
        ],
    }
    if incomplete:
        result["warnings"] = ["Older submission history not fully searched; latest report period may be incomplete"]
    if amendments:
        result.setdefault("warnings", []).append(
            "Amendments exist; original holdings returned, review amendment links before comparison"
        )
    return result


def form4(cik, filing):
    base = archive_base(cik, filing)
    document = filing.get("primaryDocument", "").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[\w.-]+\.xml", document, re.I):
        raise ValueError("Form 4 primary XML unavailable")
    root = xml_root(sec_get(f"{base}/{document}", as_json=False))
    if root.tag != "ownershipDocument" or int(root.findtext("issuer/issuerCik") or 0) != int(cik):
        raise ValueError("Form 4 issuer mismatch")
    owners = [
        {
            "name": r.findtext("reportingOwnerId/rptOwnerName"),
            "cik": r.findtext("reportingOwnerId/rptOwnerCik"),
            "officer_title": r.findtext("reportingOwnerRelationship/officerTitle"),
            "is_director": r.findtext("reportingOwnerRelationship/isDirector") in ("1", "true"),
            "is_officer": r.findtext("reportingOwnerRelationship/isOfficer") in ("1", "true"),
            "is_ten_percent_owner": r.findtext("reportingOwnerRelationship/isTenPercentOwner") in ("1", "true"),
        }
        for r in root.findall("reportingOwner")
    ]
    footnotes = {r.get("id"): "".join(r.itertext()) for r in root.findall("footnotes/footnote")}
    remarks = (root.findtext("remarks") or "").strip() or None
    rows = []
    for table, tag, derivative in [
        ("nonDerivativeTable", "nonDerivativeTransaction", False),
        ("derivativeTable", "derivativeTransaction", True),
    ]:
        for row in root.findall(f"{table}/{tag}"):
            code = row.findtext("transactionCoding/transactionCode")
            rows.append(
                {
                    "transaction_date": row.findtext("transactionDate/value"),
                    "owners": owners,
                    "security": row.findtext("securityTitle/value"),
                    "derivative": derivative,
                    "transaction_code": code,
                    "direction": {"P": "buy", "S": "sell"}.get(code, "other"),
                    "acquired_disposed": row.findtext("transactionAmounts/transactionAcquiredDisposedCode/value"),
                    "shares": numeric(row, "transactionAmounts/transactionShares/value"),
                    "price": numeric(row, "transactionAmounts/transactionPricePerShare/value"),
                    "shares_after": numeric(row, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
                    "ownership": row.findtext("ownershipNature/directOrIndirectOwnership/value"),
                    "footnotes": footnotes,
                    "footnote_refs": footnote_refs(row),
                    "remarks": remarks,
                    "filing_date": filing["filingDate"],
                    "form": filing["form"],
                    "accession": filing["accessionNumber"],
                    "filing_url": f"{base}/{document}",
                }
            )
    return rows


# Announcement categories mapped to SEC form types; categories without an SEC equivalent are rejected.
FILING_FORMS = {
    "annual": {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"},
    "quarterly": {"10-Q", "10-Q/A"},
    "current": {"8-K", "8-K/A", "6-K", "6-K/A"},
    "issuance": {"S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-1/A", "F-3", "F-3/A", "424B2", "424B3", "424B4", "424B5"},
    "reduction": {"144"},
    "inquiry": {"UPLOAD", "CORRESP"},
}
FILING_FORMS["all"] = set().union(*FILING_FORMS.values()) | {"DEF 14A", "DEFA14A"}


DCF_CONCEPTS = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
    "operating_income": ("OperatingIncomeLoss",),
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "income_tax": ("IncomeTaxExpenseBenefit",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditure": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "depreciation_amortization": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization"),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}
DCF_INSTANTS = {
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "minority_interest": ("MinorityInterest",),
}
DEBT_CONCEPTS = (
    "LongTermDebt",
    "LongTermDebtCurrent",
    "LongTermDebtNoncurrent",
    "ShortTermBorrowings",
    "CommercialPaper",
)
# Each lease kind: the total tag, else its current + noncurrent parts.
LEASE_CONCEPTS = {
    "OperatingLeaseLiability": ("OperatingLeaseLiabilityCurrent", "OperatingLeaseLiabilityNoncurrent"),
    "FinanceLeaseLiability": ("FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent"),
}


def annual_fact(row):
    if row.get("fp") != "FY" or row.get("form") not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
        return False
    if "start" not in row:
        return True
    days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
    return 300 <= days <= 400


def latest_facts(facts, names, unit):
    values = {}
    for name in names:
        concept = {}
        for row in facts.get(name, {}).get("units", {}).get(unit, []):
            if annual_fact(row) and (row["end"] not in concept or row.get("filed", "") > concept[row["end"]]["filed"]):
                concept[row["end"]] = row
        values.update({end: row for end, row in concept.items() if end not in values})
    return values


def company_facts(symbol):
    cik = resolve_cik(symbol)
    return cached(
        f"sec-companyfacts:{cik}", DAY, lambda: sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    )


def dcf_inputs(symbol, periods):
    data = company_facts(symbol)
    gaap = data.get("facts", {}).get("us-gaap", {})
    rows = {
        key: latest_facts(gaap, names, "shares" if key == "diluted_shares" else "USD")
        for key, names in {**DCF_CONCEPTS, **DCF_INSTANTS}.items()
    }
    lease_names = [name for total, parts in LEASE_CONCEPTS.items() for name in (total, *parts)]
    debt = {name: latest_facts(gaap, (name,), "USD") for name in (*DEBT_CONCEPTS, *lease_names)}
    # Values keep the latest (restated) filing; labels and cover shares come from each period's original report.
    originals = {}
    for name in DCF_CONCEPTS["revenue"] + DCF_CONCEPTS["operating_income"]:
        for row in gaap.get(name, {}).get("units", {}).get("USD", []):
            if annual_fact(row) and (row["end"] not in originals or row["filed"] < originals[row["end"]]["filed"]):
                originals[row["end"]] = row
    ends = sorted(originals, reverse=True)[:periods]
    outstanding = (
        data.get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .get("shares", [])
    )
    result = []
    for end in ends:
        entry = {key: rows[key].get(end, {}).get("val") for key in rows}
        source = originals[end]
        if (date.fromisoformat(source["filed"]) - date.fromisoformat(end)).days > 365:
            # Only a later year's comparative exists (e.g. pre-XBRL years); its labels would be wrong.
            source = {}
        parts = [debt["LongTermDebt"].get(end, {}).get("val")]
        if parts[0] is None:
            parts = [debt[name].get(end, {}).get("val") for name in ("LongTermDebtCurrent", "LongTermDebtNoncurrent")]
        parts += [debt[name].get(end, {}).get("val") for name in ("ShortTermBorrowings", "CommercialPaper")]
        leases = []
        for total, split in LEASE_CONCEPTS.items():
            names = (total,) if end in debt[total] else split
            if all(end in debt[name] for name in names):
                leases += [debt[name][end]["val"] for name in names]
        # Multi-class issuers report one cover row per class in the same filing; sum them.
        cover = [
            row
            for row in outstanding
            if source and (row.get("accn"), row.get("filed")) == (source.get("accn"), source.get("filed"))
        ]
        entry.update(
            {
                "period_end": end,
                "currency": "USD",
                "total_debt": sum(value for value in parts if value is not None)
                if any(value is not None for value in parts)
                else None,
                "lease_liabilities": sum(leases) if leases else None,
                "shares_outstanding": sum(row["val"] for row in cover) if cover else None,
                "shares_outstanding_date": max(row["end"] for row in cover) if cover else None,
                "form": source.get("form"),
                "filed": source.get("filed"),
                "fiscal_year": source.get("fy"),
            }
        )
        result.append(entry)
    if not result:
        raise ValueError("SEC annual DCF facts unavailable")
    return {
        "source": "sec_companyfacts",
        "name": data.get("entityName"),
        "periods": result,
        "warnings": [
            "SEC XBRL tags vary by issuer; null fields are filled from Yahoo for the same period when available. "
            "total_debt sums reported debt/borrowing tags and excludes leases; lease_liabilities is operating + finance "
            "leases. Amounts use the latest filed (possibly restated) value; form/filed/shares_outstanding come from "
            "the period's original annual report."
        ],
    }


def first_filed(rows, forms=("10-Q", "10-Q/A", "10-K", "10-K/A")):
    """Earliest filing per (start, end): the value the market could see then, not a later restatement."""
    kept = {}
    for row in rows:
        key = (row.get("start"), row["end"])
        if row.get("form") in forms and row.get("filed") and (key not in kept or row["filed"] < kept[key]["filed"]):
            kept[key] = row
    return list(kept.values())


def valuation_facts(symbol):
    """Point-in-time TTM net income, equity and share count events, each dated by its SEC filing date."""
    data = company_facts(symbol)
    gaap = data.get("facts", {}).get("us-gaap", {})
    income = first_filed(gaap.get("NetIncomeLoss", {}).get("units", {}).get("USD", []))

    def days(row):
        return (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days

    quarters = {row["end"]: row for row in income if "start" in row and 80 <= days(row) <= 100}
    for year in (row for row in income if "start" in row and 350 <= days(row) <= 380):
        inside = [q for q in quarters.values() if year["start"] <= q["start"] and q["end"] <= year["end"]]
        if year["end"] not in quarters and len(inside) == 3:
            quarters[year["end"]] = {
                "start": max(q["end"] for q in inside),
                "end": year["end"],
                "val": year["val"] - sum(q["val"] for q in inside),
                "filed": year["filed"],
            }
    ordered = sorted(quarters.values(), key=lambda row: row["end"])
    ttm = []
    for index in range(3, len(ordered)):
        window = ordered[index - 3 : index + 1]
        span = (date.fromisoformat(window[-1]["end"]) - date.fromisoformat(window[0]["start"])).days
        if 350 <= span <= 380:
            ttm.append(
                {
                    "filed": max(q["filed"] for q in window),
                    "end": window[-1]["end"],
                    "val": sum(q["val"] for q in window),
                }
            )
    equity = [
        {"filed": row["filed"], "end": row["end"], "val": row["val"]}
        for row in first_filed(gaap.get("StockholdersEquity", {}).get("units", {}).get("USD", []))
    ]
    shares = {}
    for row in (
        data.get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .get("shares", [])
    ):
        if row.get("form") in ("10-Q", "10-Q/A", "10-K", "10-K/A") and row.get("filed"):
            # Multi-class issuers report one row per class under the same filing; sum them.
            entry = shares.setdefault(
                (row.get("accn"), row["end"]), {"filed": row["filed"], "end": row["end"], "val": 0}
            )
            entry["val"] += row["val"]
    basis = "cover_shares_outstanding"
    if not shares:
        # Some multi-class filers (e.g. Alphabet) omit dei cover shares; weighted diluted shares are the fallback.
        basis = "weighted_diluted_shares"
        diluted = gaap.get("WeightedAverageNumberOfDilutedSharesOutstanding", {}).get("units", {}).get("shares", [])
        shares = {row["end"]: row for row in first_filed(diluted) if "start" in row and 80 <= days(row) <= 100}
    if not ttm or not equity or not shares:
        raise ValueError("SEC net income, equity or share count facts unavailable")
    return {
        "name": data.get("entityName"),
        "ttm_net_income": ttm,
        "equity": equity,
        "shares": list(shares.values()),
        "share_basis": basis,
    }


def filings(symbol, category, start, end, top):
    forms = FILING_FORMS.get(category)
    if forms is None:
        raise ValueError(
            f"category {category} has no SEC form equivalent; use all/annual/quarterly/issuance/reduction/inquiry"
        )
    identity()
    cik = resolve_cik(symbol)
    _, rows, incomplete = submissions(cik, forms, start.isoformat())
    rows = [r for r in rows if r.get("filingDate", "") <= end.isoformat()]
    items = []
    for row in rows[:top]:
        base = archive_base(cik, row)
        document = row.get("primaryDocument") or ""
        form = row["form"]
        items.append(
            {
                "title": f"{form} {row.get('primaryDocDescription') or ''}".strip(),
                "date": row.get("filingDate"),
                "report_date": row.get("reportDate") or None,
                "form": form,
                "category": next((k for k, v in FILING_FORMS.items() if k != "all" and form in v), "other"),
                "accession": row["accessionNumber"],
                "document_url": f"{base}/{document}" if re.fullmatch(r"[\w.-]+", document) else None,
                "filing_url": f"{base}/{row['accessionNumber']}-index.html",
                "pdf_url": None,
            }
        )
    return {
        "symbol": symbol,
        "cik": cik,
        "market": "US",
        "source": "sec_edgar",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total": len(rows),
        "announcements": items,
        "truncated": incomplete or len(rows) > top,
        "body_extracted": False,
        "requested_category": category,
    }


def insider_trades(args):
    identity()
    if not 1 <= args.days <= 365 or not 1 <= args.top <= 20:
        raise ValueError("days must be in 1..365 and top (filings) in 1..20")
    cik = resolve_cik(args.symbol)
    cutoff = (date.today() - timedelta(days=args.days)).isoformat()
    _, filings, incomplete = submissions(cik, {"4", "4/A"}, cutoff)
    result = {
        "symbol": args.symbol,
        "cik": cik,
        "source": "sec_edgar",
        "since_filing_date": cutoff,
        "transactions": [],
        "filings_scanned": 0,
        "truncated": incomplete or len(filings) > args.top,
        "coverage": "issuer Form 4 filings in filing-date window; top limits filings, not transactions",
    }
    for filing in filings[: args.top]:
        try:
            rows = cached(
                f"sec-form4:{cik}:{filing['accessionNumber']}", WEEK, lambda filing=filing: form4(cik, filing)
            )
            result["transactions"].extend(rows)
            result["filings_scanned"] += 1
        except Exception as exc:
            result.setdefault("warnings", []).append(f"{filing['accessionNumber']}: unavailable ({type(exc).__name__})")
    if filings and not result["filings_scanned"]:
        raise ValueError("All selected Form 4 XML documents unavailable")
    if any(r["form"] == "4/A" for r in result["transactions"]):
        result.setdefault("warnings", []).append(
            "Includes Form 4 amendments; do not double-count original and amended transactions"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("holdings")
    p.add_argument("cik")
    p.add_argument("--mode", choices=["auto", "manager", "issuer"], default="auto")
    p = sub.add_parser("insider_trades")
    p.add_argument("symbol")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--top", type=int, default=10)
    try:
        args = parser.parse_args()
        result = holdings(args) if args.command == "holdings" else insider_trades(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except SystemExit as exc:
        if exc.code:
            print(json.dumps({"error": "invalid CLI arguments"}))
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else f"SEC data unavailable ({type(exc).__name__})"
        print(json.dumps({"error": message}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    utf8_stdio()
    main()
