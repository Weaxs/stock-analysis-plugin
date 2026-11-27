from argparse import Namespace
from types import SimpleNamespace

import pytest

from tools import sec_edgar


def response(data):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data, text=data if isinstance(data, str) else "")


pytestmark = pytest.mark.usefixtures("no_sec_throttle")


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


def _filing_index(get_calls):
    def get(url, **kwargs):
        get_calls.append(url)
        if url.endswith("company_tickers.json"):
            return response({"0": {"ticker": "AAPL", "cik_str": 320193}})
        if url.endswith("CIK0000320193.json"):
            return response(
                {
                    "name": "Apple Inc.",
                    "filings": {
                        "recent": {
                            "form": ["4", "8-K", "10-Q", "10-K", "8-K"],
                            "accessionNumber": [
                                "0000320193-26-000005",
                                "0000320193-26-000004",
                                "0000320193-26-000003",
                                "0000320193-25-000002",
                                "0000320193-26-000006",
                            ],
                            "filingDate": ["2026-10-01", "2026-09-20", "2026-08-01", "2025-11-01", "2026-10-11"],
                            "reportDate": ["2026-09-30", "2026-09-18", "2026-06-27", "2025-09-27", "2026-10-09"],
                            "primaryDocument": ["form4.xml", "a8k.htm", "q.htm", "k.htm", "../evil.htm"],
                            "primaryDocDescription": ["", "8-K", "10-Q", "10-K", "8-K"],
                        },
                        "files": [],
                    },
                }
            )
        pytest.fail(f"unexpected request {url}")

    return get


def test_sec_filings_list_periodic_and_current_reports_in_window(monkeypatch):
    from datetime import date

    calls = []
    monkeypatch.setattr("requests.get", _filing_index(calls))
    result = sec_edgar.filings("AAPL", "all", date(2026, 7, 1), date(2026, 10, 10), 50)
    assert result["market"] == "US"
    assert result["source"] == "sec_edgar"
    assert [r["form"] for r in result["announcements"]] == ["8-K", "10-Q"]
    first = result["announcements"][0]
    assert first["date"] == "2026-09-20"
    assert first["category"] == "current"
    assert first["document_url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000004/a8k.htm"
    assert first["pdf_url"] is None
    assert result["truncated"] is False
    assert result["body_extracted"] is False


def test_sec_filings_category_filter_top_and_unsafe_document(monkeypatch):
    from datetime import date

    monkeypatch.setattr("requests.get", _filing_index([]))
    quarterly = sec_edgar.filings("AAPL", "quarterly", date(2025, 1, 1), date(2026, 10, 12), 50)
    assert [r["form"] for r in quarterly["announcements"]] == ["10-Q"]
    top = sec_edgar.filings("AAPL", "all", date(2025, 1, 1), date(2026, 10, 12), 1)
    assert top["total"] == 4 and top["truncated"] is True
    assert top["announcements"][0]["document_url"] is None
    assert top["announcements"][0]["filing_url"].endswith("0000320193-26-000006-index.html")


@pytest.mark.parametrize("category", ["forecast", "express", "interim", "dividend"])
def test_sec_filings_reject_categories_without_sec_form(monkeypatch, category):
    from datetime import date

    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("HTTP before category validation"))
    with pytest.raises(ValueError, match="SEC"):
        sec_edgar.filings("AAPL", category, date(2026, 1, 1), date(2026, 10, 1), 10)


def test_dcf_inputs_use_latest_annual_companyfacts(monkeypatch):
    def fact(value, end="2026-09-26", start="2025-09-28", filed="2026-11-03", form="10-K"):
        return {"start": start, "end": end, "val": value, "fy": 2026, "fp": "FY", "form": form, "filed": filed}

    def instant(value, end="2026-09-26", filed="2026-11-03", accn="a2026"):
        return {"end": end, "val": value, "fy": 2026, "fp": "FY", "form": "10-K", "filed": filed, "accn": accn}

    data = {
        "entityName": "Example",
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            instant(60, "2026-10-20"),
                            instant(38, "2026-10-20"),
                            instant(90, "2025-10-20", "2025-11-01", "a2025"),
                        ]
                    }
                }
            },
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            fact(100) | {"accn": "a2026"},
                            {**fact(80, "2025-09-27", "2024-09-29", "2025-11-01"), "accn": "a2025", "fy": 2025},
                            {**fact(85, "2025-09-27", "2024-09-29"), "accn": "a2026"},
                            {**fact(70, "2024-09-28", "2023-09-30"), "accn": "a2026"},
                        ]
                    }
                },
                "OperatingIncomeLoss": {"units": {"USD": [fact(30), fact(8, start="2026-06-28")]}},
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": {
                    "units": {"USD": [fact(25)]}
                },
                "IncomeTaxExpenseBenefit": {"units": {"USD": [fact(5)]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [fact(40)]}},
                "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [fact(12)]}},
                "DepreciationDepletionAndAmortization": {"units": {"USD": [fact(7)]}},
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [instant(20)]}},
                "LongTermDebt": {"units": {"USD": [instant(50)]}},
                "CommercialPaper": {"units": {"USD": [instant(5)]}},
                "OperatingLeaseLiability": {"units": {"USD": [instant(9)]}},
                "FinanceLeaseLiabilityCurrent": {"units": {"USD": [instant(1)]}},
                "FinanceLeaseLiabilityNoncurrent": {"units": {"USD": [instant(2), instant(4, "2025-09-27")]}},
                "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [fact(101)]}},
            },
        },
    }
    monkeypatch.setattr(sec_edgar, "resolve_cik", lambda symbol: "0000000001")
    monkeypatch.setattr(sec_edgar, "cached", lambda key, ttl, fn: data)
    result = sec_edgar.dcf_inputs("EXM", 3)
    row, older, oldest = result["periods"]
    assert older["lease_liabilities"] is None
    assert (oldest["revenue"], oldest["filed"], oldest["shares_outstanding"]) == (70, None, None)
    assert (older["revenue"], older["filed"], older["fiscal_year"]) == (85, "2025-11-01", 2025)
    assert older["shares_outstanding"] == 90
    assert result["source"] == "sec_companyfacts"
    assert row["revenue"] == 100
    assert row["operating_income"] == 30
    assert row["capital_expenditure"] == 12
    assert row["total_debt"] == 55
    assert row["lease_liabilities"] == 12
    assert row["diluted_shares"] == 101
    assert row["shares_outstanding"] == 98
    assert result["warnings"]


def test_valuation_facts_derive_q4_and_point_in_time_ttm(monkeypatch):
    def flow(start, end, value, filed, form="10-Q"):
        return {"start": start, "end": end, "val": value, "form": form, "filed": filed}

    income = [
        flow("2025-01-01", "2025-03-31", 10, "2025-04-30"),
        flow("2025-01-01", "2025-03-31", 99, "2026-04-30"),
        flow("2025-04-01", "2025-06-30", 20, "2025-07-30"),
        flow("2025-01-01", "2025-06-30", 30, "2025-07-30"),
        flow("2025-07-01", "2025-09-30", 30, "2025-10-30"),
        flow("2025-01-01", "2025-12-31", 100, "2026-02-01", "10-K"),
    ]
    data = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {"units": {"USD": income}},
                "StockholdersEquity": {
                    "units": {"USD": [{"end": "2025-12-31", "val": 400, "form": "10-K", "filed": "2026-02-01"}]}
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2026-01-20", "val": 6, "form": "10-K", "filed": "2026-02-01", "accn": "a"},
                            {"end": "2026-01-20", "val": 4, "form": "10-K", "filed": "2026-02-01", "accn": "a"},
                        ]
                    }
                }
            },
        }
    }
    monkeypatch.setattr(sec_edgar, "company_facts", lambda symbol: data)
    facts = sec_edgar.valuation_facts("EXM")
    assert facts["ttm_net_income"] == [{"filed": "2026-02-01", "end": "2025-12-31", "val": 100}]
    assert facts["shares"] == [{"filed": "2026-02-01", "end": "2026-01-20", "val": 10}]
    assert facts["share_basis"] == "cover_shares_outstanding"


def test_form4_cache_key_version_re_parses_remarks_over_legacy_cache(monkeypatch, tmp_path):
    from datetime import date, timedelta

    import _cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)
    accn = "0000320193-26-000201"
    recent_filing = (date.today() - timedelta(days=7)).isoformat()
    orig_filing = (date.today() - timedelta(days=9)).isoformat()
    tx_date = (date.today() - timedelta(days=10)).isoformat()
    # Pre-seed legacy cache entries missing `remarks` / `original_filing_date` / `acceptance_datetime`.
    for prefix in ("sec-form4", "sec-form4-v2", "sec-form4-v3"):
        sec_edgar.cached(
            f"{prefix}:0000320193:{accn}",
            sec_edgar.WEEK,
            lambda: [{"form": "4/A", "transaction_code": "S", "shares": 10}],
        )

    accept_ts_early = f"{recent_filing}T14:00:00.000Z"
    accept_ts_late = f"{recent_filing}T16:00:00.000Z"
    accn_late_small_prefix = "0000111111-26-000202"

    def get(url, **kwargs):
        if url.endswith("company_tickers.json"):
            return response({"0": {"ticker": "AAPL", "cik_str": 320193}})
        if "submissions" in url:
            return response(
                {
                    "filings": {
                        "recent": {
                            "form": ["4/A", "4/A"],
                            "accessionNumber": [accn, accn_late_small_prefix],
                            "filingDate": [recent_filing, recent_filing],
                            "acceptanceDateTime": [accept_ts_early, accept_ts_late],
                            "reportDate": [tx_date, tx_date],
                            "primaryDocument": ["form4a.xml", "form4a2.xml"],
                        },
                        "files": [],
                    }
                }
            )
        return response(
            f"""<ownershipDocument><dateOfOriginalSubmission>{orig_filing}</dateOfOriginalSubmission><issuer><issuerCik>0000320193</issuerCik></issuer><nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>{tx_date}</value></transactionDate><transactionCoding><transactionCode>S</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>10</value></transactionShares><transactionPricePerShare><value>150</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable><remarks>Amending to report an inadvertently omitted sale.</remarks></ownershipDocument>"""
        )

    monkeypatch.setattr("requests.get", get)
    result = sec_edgar.insider_trades(Namespace(symbol="AAPL", days=90, top=10))
    # Same-day cross-submitter filings are sorted by acceptanceDateTime descending (16:00Z before 14:00Z).
    assert [tx["accession"] for tx in result["transactions"]] == [accn_late_small_prefix, accn]
    assert result["transactions"][0]["acceptance_datetime"] == accept_ts_late
    assert result["transactions"][1]["acceptance_datetime"] == accept_ts_early
    assert result["transactions"][1]["remarks"] == "Amending to report an inadvertently omitted sale."
    assert result["transactions"][1]["original_filing_date"] == orig_filing
    v4_rows = sec_edgar.cached(
        f"sec-form4-v4:0000320193:{accn}",
        sec_edgar.WEEK,
        lambda: pytest.fail("expected v4 Form 4 cache entry to be populated"),
    )
    assert v4_rows[0]["remarks"] == "Amending to report an inadvertently omitted sale."
    assert v4_rows[0]["original_filing_date"] == orig_filing
    assert v4_rows[0]["acceptance_datetime"] == accept_ts_early
