#!/usr/bin/env python3
"""SEC institutional 13F and issuer Form 4 disclosures, never realtime holdings."""

import argparse
import json
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


def identity():
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if "\n" in agent or "\r" in agent or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", agent):
        raise ValueError("Set SEC_USER_AGENT to your application name and real contact email")
    return agent


def sec_get(url, as_json=True):
    global _last_request
    agent = identity()
    time.sleep(max(0, 0.5 - (time.monotonic() - _last_request)))
    _last_request = time.monotonic()
    try:
        response = requests.get(url, headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"}, timeout=20)
        response.raise_for_status()
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
    loaded = 0
    # shortcut: at most three older submission shards; expose incomplete coverage when more are needed.
    for file in relevant[:3]:
        if not cutoff and any(r.get("form") == "13F-HR" for r in rows):
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
    return data.get("name"), result, bool(cutoff and loaded < len(relevant))


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
        return result if result == result and abs(result) != float("inf") else None
    except (ValueError, TypeError):
        return None


def holdings(args):
    identity()
    if not re.fullmatch(r"\d{1,10}", args.cik) or int(args.cik) == 0:
        raise ValueError("13F requires an institutional manager CIK (1..10 digits)")
    cik = args.cik.zfill(10)
    name, filings, _ = submissions(cik, {"13F-HR", "13F-HR/A"})
    originals = [r for r in filings if r["form"] == "13F-HR"]
    if not originals:
        raise ValueError("No original 13F-HR in available submission history; issuer CIK is not a holder index")
    filing = max(originals, key=lambda r: (r.get("reportDate", ""), r.get("filingDate", "")))
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
    if amendments:
        result["warnings"] = ["Amendments exist; original holdings returned, review amendment links before comparison"]
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
                    "filing_date": filing["filingDate"],
                    "form": filing["form"],
                    "accession": filing["accessionNumber"],
                    "filing_url": f"{base}/{document}",
                }
            )
    return rows


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
