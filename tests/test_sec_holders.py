import csv
import io
import zipfile
from argparse import Namespace
from types import SimpleNamespace

import pytest

from tools import sec_edgar


def bulk_zip(submissions, covers, positions):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, rows in [("SUBMISSION", submissions), ("COVERPAGE", covers), ("INFOTABLE", positions)]:
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(name + ".tsv", stream.getvalue())
    return output.getvalue()


@pytest.fixture
def issuer_source(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "stock-analysis-plugin test@example.com")
    filings = {
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "form": ["SCHEDULE 13G"],
                "accessionNumber": ["0002100119-26-000139"],
                "filingDate": ["2026-04-29"],
                "primaryDocument": ["xslSCHEDULE_13G_X02/primary_doc.xml"],
            },
            "files": [],
        },
    }
    catalog = '<a href="/files/data/01jun2026-31aug2026_form13f.zip">Latest</a>'
    xml = "<edgarSubmission><formData><coverPageHeader><securitiesClassTitle>Common Stock</securitiesClassTitle><issuerInfo><issuerCik>0000320193</issuerCik><issuerCusips><issuerCusipNumber>037833100</issuerCusipNumber></issuerCusips></issuerInfo></coverPageHeader></formData></edgarSubmission>"

    def configure(submissions, covers, positions, issuer_cik="0000320193"):
        source_xml = xml.replace("0000320193", issuer_cik)
        blob = bulk_zip(submissions, covers, positions)

        def get(url, **kwargs):
            if "submissions" in url:
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: filings)
            if url.endswith(".xml"):
                return SimpleNamespace(raise_for_status=lambda: None, text=source_xml)
            if url.endswith(".zip"):
                return SimpleNamespace(
                    raise_for_status=lambda: None, iter_content=lambda **k: iter([blob]), close=lambda: None
                )
            return SimpleNamespace(raise_for_status=lambda: None, text=catalog)

        monkeypatch.setattr("requests.get", get)

    return configure


def test_issuer_reverse_lookup_uses_exact_cusip_and_restatement(issuer_source):
    issuer_source(
        [
            {
                "ACCESSION_NUMBER": "0000000001-26-000001",
                "CIK": "1",
                "FILING_DATE": "14-AUG-2026",
                "PERIODOFREPORT": "30-JUN-2026",
                "SUBMISSIONTYPE": "13F-HR",
            },
            {
                "ACCESSION_NUMBER": "0000000001-26-000002",
                "CIK": "1",
                "FILING_DATE": "15-AUG-2026",
                "PERIODOFREPORT": "30-JUN-2026",
                "SUBMISSIONTYPE": "13F-HR/A",
            },
            {
                "ACCESSION_NUMBER": "0000000002-26-000001",
                "CIK": "2",
                "FILING_DATE": "14-AUG-2026",
                "PERIODOFREPORT": "30-JUN-2026",
                "SUBMISSIONTYPE": "13F-HR",
            },
        ],
        [
            {"ACCESSION_NUMBER": "0000000001-26-000001", "FILINGMANAGER_NAME": "Manager One", "AMENDMENTTYPE": ""},
            {
                "ACCESSION_NUMBER": "0000000001-26-000002",
                "FILINGMANAGER_NAME": "Manager One",
                "AMENDMENTTYPE": "RESTATEMENT",
            },
            {"ACCESSION_NUMBER": "0000000002-26-000001", "FILINGMANAGER_NAME": "Manager Two", "AMENDMENTTYPE": ""},
        ],
        [
            {
                "ACCESSION_NUMBER": "0000000001-26-000001",
                "CUSIP": "037833100",
                "VALUE": "1000",
                "SSHPRNAMT": "10",
                "SSHPRNAMTTYPE": "SH",
                "PUTCALL": "",
                "TITLEOFCLASS": "COM",
            },
            {
                "ACCESSION_NUMBER": "0000000001-26-000002",
                "CUSIP": "037833100",
                "VALUE": "2000",
                "SSHPRNAMT": "20",
                "SSHPRNAMTTYPE": "SH",
                "PUTCALL": "",
                "TITLEOFCLASS": "COM",
            },
            {
                "ACCESSION_NUMBER": "0000000002-26-000001",
                "CUSIP": "999999999",
                "VALUE": "5000",
                "SSHPRNAMT": "50",
                "SSHPRNAMTTYPE": "SH",
                "PUTCALL": "",
                "TITLEOFCLASS": "COM",
            },
        ],
    )
    result = sec_edgar.holdings(Namespace(cik="0000320193", mode="issuer"))
    assert result["query_type"] == "issuer"
    assert result["cusips"] == ["037833100"]
    assert result["report_date"] == "2026-06-30"
    assert result["filing_window_end"] == "2026-08-31"
    assert result["institution_count"] == 1
    assert result["institutions"][0]["cik"] == "0000000001"
    assert result["institutions"][0]["shares"] == 20
    assert result["institutions"][0]["reported_value_usd"] == 2000
    assert result["mapping_complete"] is False
    assert result["realtime"] is False


def report(accession, cik, form="13F-HR", amendment="", cusip="037833100", shares="10", value="1000", option=""):
    return (
        {
            "ACCESSION_NUMBER": accession,
            "CIK": cik,
            "FILING_DATE": "14-AUG-2026",
            "PERIODOFREPORT": "30-JUN-2026",
            "SUBMISSIONTYPE": form,
        },
        {"ACCESSION_NUMBER": accession, "FILINGMANAGER_NAME": "Manager " + cik, "AMENDMENTTYPE": amendment},
        {
            "ACCESSION_NUMBER": accession,
            "CUSIP": cusip,
            "VALUE": value,
            "SSHPRNAMT": shares,
            "SSHPRNAMTTYPE": "SH",
            "PUTCALL": option,
            "TITLEOFCLASS": "COM",
        },
    )


def source_reports(source, *rows):
    source(*[list(values) for values in zip(*rows)])


def test_missing_base_for_unrelated_amendment_prevents_completeness_claim(issuer_source):
    source_reports(
        issuer_source,
        report("0000000001-26-000001", "1"),
        report("0000000002-26-000001", "2", "13F-HR/A", "NEW HOLDINGS", cusip="999999999"),
    )
    result = sec_edgar.holdings(Namespace(cik="320193", mode="issuer"))
    assert result["institution_count"] == 1
    assert result["complete_for_mapped_cusips"] is False
    assert result["unresolved_manager_count"] == 1


def test_unknown_matching_amendment_is_returned_without_invented_totals(issuer_source):
    source_reports(
        issuer_source, report("0000000001-26-000001", "1"), report("0000000002-26-000001", "2", "13F-HR/A", "")
    )
    result = sec_edgar.holdings(Namespace(cik="320193", mode="issuer"))
    unknown = next(r for r in result["institutions"] if r["cik"] == "0000000002")
    assert unknown["shares"] is None and unknown["reported_value_usd"] is None
    assert unknown["positions"][0]["amendment_applied"] is False
    assert result["complete_for_mapped_cusips"] is False


def test_new_holdings_are_added_and_options_do_not_inflate_share_count(issuer_source):
    source_reports(
        issuer_source,
        report("0000000001-26-000001", "1"),
        report("0000000001-26-000002", "1", "13F-HR/A", "NEW HOLDINGS", shares="5", value="500"),
        report("0000000001-26-000003", "1", "13F-HR/A", "NEW HOLDINGS", shares="7", value="700", option="CALL"),
    )
    result = sec_edgar.holdings(Namespace(cik="320193", mode="issuer"))
    holder = result["institutions"][0]
    assert holder["shares"] == 15
    assert holder["reported_value_usd"] == 2200
    assert len(holder["positions"]) == 3
    assert result["complete_for_mapped_cusips"] is True


def test_nonfinite_reported_value_is_rejected(issuer_source):
    source_reports(issuer_source, report("0000000001-26-000001", "1", value="NaN"))
    with pytest.raises(ValueError, match="nonfinite"):
        sec_edgar.holdings(Namespace(cik="320193", mode="issuer"))


def test_mapping_requires_exact_issuer_cik_even_when_name_matches(issuer_source):
    rows = report("0000000001-26-000001", "1")
    issuer_source([rows[0]], [rows[1]], [rows[2]], issuer_cik="0000000001")
    with pytest.raises(ValueError, match="No verified issuer"):
        sec_edgar.holdings(Namespace(cik="320193", mode="issuer"))
