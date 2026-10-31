"""Exact issuer CUSIPs joined to the latest published SEC 13F bulk window."""

import csv
import hashlib
import io
import math
import re
import time
import zipfile
from collections import Counter
from datetime import date
from xml.etree import ElementTree

import _cache
from _cache import atomic_write, file_lock
from sec_edgar import archive_base, sec_get, xml_root

CATALOG = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def sec_date(value):
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value).isoformat()
    day, month, year = value.lower().split("-")
    return date(int(year), MONTHS.index(month) + 1, int(day)).isoformat()


def issuer_cusips(cik, data):
    recent = data["filings"]["recent"]
    forms = recent.get("form", [])
    candidates = [
        i
        for i, form in enumerate(forms)
        if form
        in (
            "SCHEDULE 13G",
            "SCHEDULE 13G/A",
            "SCHEDULE 13D",
            "SCHEDULE 13D/A",
            "SC 13G",
            "SC 13G/A",
            "SC 13D",
            "SC 13D/A",
        )
    ]
    evidence = []
    for i in candidates[:10]:
        document = recent["primaryDocument"][i].rsplit("/", 1)[-1]
        if not re.fullmatch(r"[\w.-]+\.xml", document, re.I):
            continue
        filing = {"accessionNumber": recent["accessionNumber"][i]}
        url = f"{archive_base(cik, filing)}/{document}"
        try:
            root = xml_root(sec_get(url, as_json=False))
        except (ValueError, ElementTree.ParseError):
            continue
        issuer = root.find(".//issuerInfo")
        if issuer is None:
            continue
        identifier = (issuer.findtext("issuerCik") or issuer.findtext("issuerCIK") or "").strip()
        if not identifier.isdigit() or int(identifier) != int(cik):
            continue
        for node in issuer.findall(".//issuerCusipNumber"):
            cusip = (node.text or "").strip().upper()
            if re.fullmatch(r"[A-Z0-9*@#]{8}\d", cusip):
                evidence.append(
                    {
                        "cusip": cusip,
                        "source_url": url,
                        "filing_date": recent["filingDate"][i],
                        "security_class": root.findtext(".//securitiesClassTitle"),
                    }
                )
    if not evidence:
        raise ValueError("No verified issuer CIK/CUSIP mapping in recent structured SEC Schedule 13D/G filings")
    return evidence


def latest_bulk():
    html = sec_get(CATALOG, as_json=False)
    windows = []
    for href in re.findall(r'href=["\']([^"\']+\.zip)["\']', html, re.I):
        match = re.search(r"(\d{2}[a-z]{3}\d{4})-(\d{2}[a-z]{3}\d{4})_form13f\.zip$", href, re.I)
        if not match:
            continue

        start, end = (sec_date(f"{value[:2]}-{value[2:5]}-{value[5:]}") for value in match.groups())
        if href.startswith("/files/"):
            url = "https://www.sec.gov" + href
        elif href.startswith("https://www.sec.gov/files/"):
            url = href
        else:
            continue
        if end <= date.today().isoformat():
            windows.append({"url": url, "start": start, "end": end})
    if not windows:
        raise ValueError("SEC 13F bulk catalog returned no published data window")
    return max(windows, key=lambda w: w["end"])


def _fresh_archive(path):
    try:
        return 0 <= time.time() - path.stat().st_mtime < _cache.WEEK and zipfile.is_zipfile(path)
    except OSError:
        return False


def bulk_file(window):
    path = _cache.CACHE_DIR / f"sec-13f-{hashlib.sha256(window['url'].encode()).hexdigest()[:16]}.zip"
    if _fresh_archive(path):
        return path
    try:
        with file_lock(path.with_suffix(".lock")):
            if _fresh_archive(path):
                return path
            response = sec_get(window["url"], stream=True)
            try:
                with atomic_write(path, binary=True) as out:
                    size = 0
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        size += len(chunk)
                        if size > 256 * 1024 * 1024:
                            raise ValueError("SEC bulk archive exceeds the 256 MiB download limit")
                        out.write(chunk)
                    out.flush()
                    if not zipfile.is_zipfile(out):
                        raise ValueError("SEC bulk download is not a valid ZIP archive")
            finally:
                response.close()
            return path
    except OSError as exc:
        raise ValueError(f"SEC bulk archive unavailable ({type(exc).__name__})") from None


def table_rows(archive, table):
    names = [n for n in archive.namelist() if n.rsplit("/", 1)[-1].upper() == table + ".TSV"]
    if len(names) != 1 or archive.getinfo(names[0]).file_size > 2 * 1024**3:
        raise ValueError(f"SEC bulk {table} table missing or too large")
    with archive.open(names[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as text:
        reader = csv.DictReader(text, delimiter="\t")
        if not reader.fieldnames or "ACCESSION_NUMBER" not in reader.fieldnames:
            raise ValueError(f"SEC bulk {table} schema unavailable")
        yield from reader


def scan_holders(path, cusips):
    with zipfile.ZipFile(path) as archive:
        submissions = {r["ACCESSION_NUMBER"]: r for r in table_rows(archive, "SUBMISSION")}
        covers = {r["ACCESSION_NUMBER"]: r for r in table_rows(archive, "COVERPAGE")}
        periods = Counter(
            sec_date(r["PERIODOFREPORT"]) for r in submissions.values() if r["SUBMISSIONTYPE"] == "13F-HR"
        )
        if not periods:
            raise ValueError("SEC bulk contains no original 13F-HR reports")
        # The modal report period is the complete quarterly cohort; late older reports occur in every archive.
        period = max(periods, key=lambda p: (periods[p], p))
        managers = {}
        for accession, row in submissions.items():
            if row["SUBMISSIONTYPE"] not in ("13F-HR", "13F-HR/A") or sec_date(row["PERIODOFREPORT"]) != period:
                continue
            row = {**row, "date": sec_date(row["FILING_DATE"]), "accession": accession, **covers.get(accession, {})}
            managers.setdefault(row["CIK"].zfill(10), []).append(row)
        by_accession = {row["accession"]: (cik, row) for cik, filings in managers.items() for row in filings}
        selected = set()
        incomplete = set()
        for cik, filings in managers.items():
            bases = [r for r in filings if r["SUBMISSIONTYPE"] == "13F-HR" or r.get("AMENDMENTTYPE") == "RESTATEMENT"]
            base = max(bases, key=lambda r: (r["date"], r["accession"])) if bases else None
            if base:
                selected.add(base["accession"])
            else:
                incomplete.add(cik)
            for r in filings:
                if r["SUBMISSIONTYPE"] == "13F-HR/A" and (
                    not base or (r["date"], r["accession"]) > (base["date"], base["accession"])
                ):
                    if r.get("AMENDMENTTYPE") == "NEW HOLDINGS":
                        selected.add(r["accession"])
                    else:
                        incomplete.add(cik)
        result = {}
        # shortcut: stream one published archive per uncached CUSIP set; use a persistent DB if query volume warrants it.
        for row in table_rows(archive, "INFOTABLE"):
            accession = row["ACCESSION_NUMBER"]
            if row["CUSIP"].strip().upper() not in cusips or accession not in by_accession:
                continue
            cik, filing = by_accession[accession]
            if accession not in selected and cik not in incomplete:
                continue
            holder = result.setdefault(
                cik,
                {
                    "cik": cik,
                    "name": filing.get("FILINGMANAGER_NAME"),
                    "positions": [],
                    "holdings_complete": cik not in incomplete,
                },
            )
            shares, value = float(row["SSHPRNAMT"]), float(row["VALUE"])
            if not math.isfinite(shares) or not math.isfinite(value):
                raise ValueError("SEC bulk contains nonfinite holding amounts")
            multiplier = 1 if filing["date"] >= "2023-01-03" else 1000
            holder["positions"].append(
                {
                    "cusip": row["CUSIP"].strip().upper(),
                    "security_class": row.get("TITLEOFCLASS"),
                    "shares": shares,
                    "share_type": row["SSHPRNAMTTYPE"],
                    "put_call": row.get("PUTCALL") or None,
                    "reported_value_usd": value * multiplier,
                    "filing_date": filing["date"],
                    "accession": accession,
                    "form": filing["SUBMISSIONTYPE"],
                    "amendment_applied": accession in selected,
                    "filing_url": f"{archive_base(cik, {'accessionNumber': accession})}/{accession}-index.html",
                }
            )
        for holder in result.values():
            positions = holder["positions"]
            holder["shares"] = (
                sum(r["shares"] for r in positions if r["share_type"] == "SH" and not r["put_call"])
                if holder["holdings_complete"]
                else None
            )
            holder["reported_value_usd"] = (
                sum(r["reported_value_usd"] for r in positions) if holder["holdings_complete"] else None
            )
        return (
            period,
            len(incomplete),
            sorted(result.values(), key=lambda r: (-(r["reported_value_usd"] or 0), r["cik"])),
        )


def issuer_holders(cik, data):
    evidence = issuer_cusips(cik, data)
    cusips = sorted({r["cusip"] for r in evidence})
    window = latest_bulk()

    def fetch():
        period, unresolved, holders = scan_holders(bulk_file(window), set(cusips))
        return {"report_date": period, "institutions": holders, "unresolved_manager_count": unresolved}

    result = _cache.cached(f"sec-issuer-holders:v2:{window['url']}:{','.join(cusips)}", _cache.DAY, fetch)
    return {
        "cik": cik,
        "issuer": data.get("name"),
        "query_type": "issuer",
        "source": "sec_edgar",
        "cusips": cusips,
        "cusip_evidence": evidence,
        **result,
        "institution_count": len(result["institutions"]),
        "bulk_source_url": window["url"],
        "filing_window_start": window["start"],
        "filing_window_end": window["end"],
        "mapping_complete": False,
        "complete_for_mapped_cusips": result["unresolved_manager_count"] == 0,
        "realtime": False,
        "value_unit": "USD",
        "coverage": "Published SEC 13F window for verified CUSIPs; mapping may omit other security classes/CUSIP changes; late filings, confidential positions and subsequent amendments are not included",
        "shares_basis": "SH equity positions only; options and principal amounts remain separate in positions",
    }
