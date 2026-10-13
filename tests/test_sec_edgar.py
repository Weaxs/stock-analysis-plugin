from argparse import Namespace
from types import SimpleNamespace

import pytest

from tools import sec_edgar


def response(data):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data, text=data if isinstance(data, str) else "")


@pytest.fixture(autouse=True)
def sec_identity(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "stock-analysis-plugin test@example.com")


def test_latest_13f_reads_namespaced_xml_and_reports_units(monkeypatch):
    urls = []

    def get(url, **kwargs):
        urls.append(url)
        assert kwargs["headers"]["User-Agent"] == "stock-analysis-plugin test@example.com"
        if "submissions" in url:
            return response(
                {
                    "name": "Manager",
                    "filings": {
                        "recent": {
                            "form": ["13F-HR", "13F-HR"],
                            "accessionNumber": ["0001067983-26-000001", "0001067983-25-000001"],
                            "filingDate": ["2026-08-14", "2025-11-14"],
                            "reportDate": ["2026-06-30", "2025-09-30"],
                            "primaryDocument": ["primary.xml", "primary.xml"],
                        },
                        "files": [],
                    },
                }
            )
        if url.endswith("index.json"):
            return response({"directory": {"item": [{"name": "primary.xml"}, {"name": "table.xml"}]}})
        if url.endswith("primary.xml"):
            return response("<edgarSubmission/>")
        return response(
            '<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"><infoTable><nameOfIssuer>APPLE</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>100000</value><shrsOrPrnAmt><sshPrnamt>500</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><investmentDiscretion>SOLE</investmentDiscretion></infoTable></informationTable>'
        )

    monkeypatch.setattr("requests.get", get)
    result = sec_edgar.holdings(Namespace(cik="0001067983"))
    assert result["report_date"] == "2026-06-30"
    assert result["holdings"][0]["cusip"] == "037833100"
    assert result["holdings"][0]["shares"] == 500
    assert result["value_unit"] == "USD"
    assert result["disclosure_lag_days"] == 45
    assert result["realtime"] is False
    assert len(urls) == 4


def test_form4_preserves_transaction_codes_and_owner_relationships(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("company_tickers.json"):
            return response({"0": {"ticker": "AAPL", "cik_str": 320193}})
        if "submissions" in url:
            return response(
                {
                    "filings": {
                        "recent": {
                            "form": ["4"],
                            "accessionNumber": ["0000320193-26-000001"],
                            "filingDate": ["2026-10-12"],
                            "reportDate": ["2026-10-10"],
                            "primaryDocument": ["xslF345X05/form4.xml"],
                        },
                        "files": [],
                    }
                }
            )
        assert "/xsl" not in url
        return response(
            """<ownershipDocument><issuer><issuerCik>0000320193</issuerCik></issuer><reportingOwner><reportingOwnerId><rptOwnerName>Jane</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner><nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-10-10</value></transactionDate><transactionCoding><transactionCode>A</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>100</value></transactionShares><transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable><footnotes><footnote id="F1">Grant, not open-market purchase.</footnote></footnotes></ownershipDocument>"""
        )

    monkeypatch.setattr("requests.get", get)
    result = sec_edgar.insider_trades(Namespace(symbol="AAPL", days=90, top=10))
    row = result["transactions"][0]
    assert row["owners"][0]["name"] == "Jane"
    assert row["owners"][0]["officer_title"] == "CEO"
    assert row["transaction_code"] == "A"
    assert row["direction"] == "other"
    assert row["acquired_disposed"] == "A"
    assert row["price"] is None
    assert row["footnotes"]["F1"] == "Grant, not open-market purchase."


def test_sec_requires_real_identity_before_http(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT")
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("HTTP before identity validation"))
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        sec_edgar.holdings(Namespace(cik="1067983"))


@pytest.mark.parametrize("cik", ["0", "00000000000", "AAPL", "../123"])
def test_invalid_manager_cik(cik):
    with pytest.raises(ValueError):
        sec_edgar.holdings(Namespace(cik=cik))


def test_form4_older_shards_and_failure_are_explicit(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("CIK0000320193.json"):
            return response(
                {
                    "filings": {
                        "recent": {"accessionNumber": []},
                        "files": [{"name": "CIK0000320193-submissions-001.json", "filingTo": "2026-10-12"}],
                    }
                }
            )
        if url.endswith("submissions-001.json"):
            return response(
                {
                    "form": ["4"],
                    "accessionNumber": ["0000320193-26-000001"],
                    "filingDate": ["2026-10-12"],
                    "primaryDocument": ["bad.htm"],
                }
            )
        pytest.fail("unexpected request")

    monkeypatch.setattr("requests.get", get)
    with pytest.raises(ValueError, match="All selected Form 4"):
        sec_edgar.insider_trades(Namespace(symbol="320193", days=90, top=10))


def test_form4_buy_and_sell_are_distinct_from_acquisition_code(monkeypatch):
    xml = """<ownershipDocument><issuer><issuerCik>320193</issuerCik></issuer><nonDerivativeTable><nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>2</value></transactionShares><transactionPricePerShare><value>100.5</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable><derivativeTable><derivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding></derivativeTransaction></derivativeTable></ownershipDocument>"""
    monkeypatch.setattr("requests.get", lambda *a, **k: response(xml))
    rows = sec_edgar.form4(
        "0000320193",
        {
            "accessionNumber": "0000320193-26-000001",
            "primaryDocument": "f.xml",
            "filingDate": "2026-10-12",
            "form": "4/A",
        },
    )
    assert rows[0]["direction"] == "buy" and rows[0]["price"] == 100.5
    assert rows[1]["direction"] == "sell" and rows[1]["derivative"] is True
    assert rows[0]["form"] == "4/A"


def test_cli_missing_identity_is_json_error():
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = os.environ.copy()
    env.pop("SEC_USER_AGENT", None)
    run = subprocess.run(
        [sys.executable, str(Path(sec_edgar.__file__)), "holdings", "1067983"], capture_output=True, text=True, env=env
    )
    assert run.returncode == 1
    assert "SEC_USER_AGENT" in json.loads(run.stdout)["error"]
    assert "Traceback" not in run.stderr


def test_form4_preserves_field_footnote_references(monkeypatch):
    xml = """<ownershipDocument><issuer><issuerCik>320193</issuerCik></issuer><nonDerivativeTable><nonDerivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding><transactionAmounts><transactionPricePerShare><value>10</value><footnoteId id="F2"/></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable><footnotes><footnote id="F1">Shares indirectly owned.</footnote><footnote id="F2">Weighted average sale price.</footnote></footnotes></ownershipDocument>"""
    monkeypatch.setattr("requests.get", lambda *a, **k: response(xml))
    rows = sec_edgar.form4(
        "0000320193",
        {
            "accessionNumber": "0000320193-26-000001",
            "primaryDocument": "f.xml",
            "filingDate": "2026-10-12",
            "form": "4",
        },
    )
    assert rows[0]["footnote_refs"] == {"transactionAmounts/transactionPricePerShare": ["F2"]}


def test_13f_amendment_only_recent_reads_older_original(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("CIK0001067983.json"):
            return response(
                {
                    "name": "Manager",
                    "filings": {
                        "recent": {
                            "form": ["13F-HR/A"],
                            "accessionNumber": ["0001067983-26-000002"],
                            "filingDate": ["2026-09-01"],
                            "reportDate": ["2026-06-30"],
                            "primaryDocument": ["primary.xml"],
                        },
                        "files": [{"name": "CIK0001067983-submissions-001.json", "filingTo": "2026-08-14"}],
                    },
                }
            )
        if url.endswith("submissions-001.json"):
            return response(
                {
                    "form": ["13F-HR"],
                    "accessionNumber": ["0001067983-26-000001"],
                    "filingDate": ["2026-08-14"],
                    "reportDate": ["2026-06-30"],
                    "primaryDocument": ["primary.xml"],
                }
            )
        if url.endswith("index.json"):
            return response({"directory": {"item": [{"name": "table.xml"}]}})
        return response(
            "<informationTable><infoTable><nameOfIssuer>APPLE</nameOfIssuer><cusip>037833100</cusip><value>100</value></infoTable></informationTable>"
        )

    monkeypatch.setattr("requests.get", get)
    result = sec_edgar.holdings(Namespace(cik="1067983"))
    assert result["accession"] == "0001067983-26-000001"
    assert result["amendments"][0]["accession"] == "0001067983-26-000002"
    assert result["warnings"]


def test_13f_ignores_unrelated_malformed_xml_before_information_table(monkeypatch):
    def get(url, **kwargs):
        if "submissions" in url:
            return response(
                {
                    "filings": {
                        "recent": {
                            "form": ["13F-HR"],
                            "accessionNumber": ["0001067983-26-000001"],
                            "filingDate": ["2026-08-14"],
                            "reportDate": ["2026-06-30"],
                        },
                        "files": [],
                    }
                }
            )
        if url.endswith("index.json"):
            return response({"directory": {"item": [{"name": "unrelated.xml"}, {"name": "table.xml"}]}})
        if url.endswith("unrelated.xml"):
            return response("not XML")
        return response(
            "<informationTable><infoTable><nameOfIssuer>APPLE</nameOfIssuer><cusip>037833100</cusip><value>100</value></infoTable></informationTable>"
        )

    monkeypatch.setattr("requests.get", get)
    result = sec_edgar.holdings(Namespace(cik="1067983"))
    assert result["holdings"][0]["cusip"] == "037833100"


def test_auto_mode_preserves_archived_manager_without_recent_13f(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("CIK0001067983.json"):
            return response(
                {
                    "name": "Manager",
                    "filings": {
                        "recent": {
                            "form": ["8-K"],
                            "accessionNumber": ["0001067983-26-000002"],
                            "filingDate": ["2026-09-01"],
                            "reportDate": ["2026-06-30"],
                            "primaryDocument": ["primary.htm"],
                        },
                        "files": [{"name": "CIK0001067983-submissions-001.json", "filingTo": "2026-08-14"}],
                    },
                }
            )
        if url.endswith("submissions-001.json"):
            return response(
                {
                    "form": ["13F-HR"],
                    "accessionNumber": ["0001067983-26-000001"],
                    "filingDate": ["2026-08-14"],
                    "reportDate": ["2026-06-30"],
                    "primaryDocument": ["primary.xml"],
                }
            )
        if url.endswith("index.json"):
            return response({"directory": {"item": [{"name": "table.xml"}]}})
        return response(
            "<informationTable><infoTable><nameOfIssuer>APPLE</nameOfIssuer><cusip>037833100</cusip><value>100</value></infoTable></informationTable>"
        )

    monkeypatch.setattr("requests.get", get)
    result = sec_edgar.holdings(Namespace(cik="1067983"))
    assert result["holdings"][0]["cusip"] == "037833100"


def test_manager_uses_original_for_latest_known_amended_quarter(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("CIK0001067983.json"):
            return response(
                {
                    "name": "Manager",
                    "filings": {
                        "recent": {
                            "form": ["13F-HR/A", "13F-HR"],
                            "accessionNumber": ["0001067983-26-000003", "0001067983-26-000001"],
                            "filingDate": ["2026-09-01", "2026-08-20"],
                            "reportDate": ["2026-06-30", "2025-12-31"],
                            "primaryDocument": ["primary.xml", "primary.xml"],
                        },
                        "files": [{"name": "CIK0001067983-submissions-001.json", "filingTo": "2026-08-14"}],
                    },
                }
            )
        if url.endswith("submissions-001.json"):
            return response(
                {
                    "form": ["13F-HR"],
                    "accessionNumber": ["0001067983-26-000002"],
                    "filingDate": ["2026-08-14"],
                    "reportDate": ["2026-06-30"],
                    "primaryDocument": ["primary.xml"],
                }
            )
        if url.endswith("index.json"):
            return response({"directory": {"item": [{"name": "table.xml"}]}})
        return response(
            "<informationTable><infoTable><nameOfIssuer>APPLE</nameOfIssuer><cusip>037833100</cusip><value>100</value></infoTable></informationTable>"
        )

    monkeypatch.setattr("requests.get", get)
    result = sec_edgar.holdings(Namespace(cik="1067983", mode="manager"))
    assert result["report_date"] == "2026-06-30"
    assert result["accession"] == "0001067983-26-000002"


def test_missing_original_for_latest_period_does_not_return_old_quarter(monkeypatch):
    metadata = {
        "filings": {
            "recent": {
                "form": ["13F-HR/A", "13F-HR"],
                "accessionNumber": ["0001067983-26-000003", "0001067983-26-000001"],
                "filingDate": ["2026-09-01", "2026-08-20"],
                "reportDate": ["2026-06-30", "2025-12-31"],
            },
            "files": [],
        }
    }

    def get(url, **kwargs):
        assert "submissions" in url, "must not fetch an older quarter as latest"
        return response(metadata)

    monkeypatch.setattr("requests.get", get)
    with pytest.raises(ValueError, match="Latest 13F report period"):
        sec_edgar.holdings(Namespace(cik="1067983", mode="manager"))


def test_unsearched_recent_history_is_explicitly_truncated(monkeypatch):
    files = [{"name": f"CIK0001067983-submissions-00{i}.json", "filingTo": "2026-07-01"} for i in range(1, 5)]

    def get(url, **kwargs):
        if url.endswith("CIK0001067983.json"):
            return response(
                {
                    "filings": {
                        "recent": {
                            "form": ["13F-HR"],
                            "accessionNumber": ["0001067983-26-000001"],
                            "filingDate": ["2026-08-14"],
                            "reportDate": ["2026-06-30"],
                        },
                        "files": files,
                    }
                }
            )
        if "submissions-00" in url:
            return response({"accessionNumber": []})
        if url.endswith("index.json"):
            return response({"directory": {"item": [{"name": "table.xml"}]}})
        return response(
            "<informationTable><infoTable><cusip>037833100</cusip><value>100</value></infoTable></informationTable>"
        )

    monkeypatch.setattr("requests.get", get)
    result = sec_edgar.holdings(Namespace(cik="1067983", mode="manager"))
    assert result["history_truncated"] is True
    assert any("not fully searched" in warning for warning in result["warnings"])
