import csv
import io
import json
import subprocess
import sys
import zipfile
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from tools import credit_data

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise OSError("test did not configure this HTTP source")

    monkeypatch.setattr("requests.get", blocked)
    monkeypatch.setattr("requests.post", blocked)
    monkeypatch.setattr(credit_data, "cached", lambda key, ttl, fetch: fetch())


class Response:
    def __init__(self, payload=None, content=b""):
        self.payload, self.content = payload, content

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def trade(name="CDX.NA.IG", spread="0.0060", maturity="2031-12-20", **overrides):
    row = {
        "UPI Underlier Name": name,
        "UPI FISN": "NA/CDS Corp Idx",
        "Action type": "NEWT",
        "Event type": "TRAD",
        "Package indicator": "FALSE",
        "Execution Timestamp": "2026-11-16T20:00:00Z",
        "Expiration Date": maturity,
        "Spread-Leg 1": spread,
        "Spread notation-Leg 1": "3",
    }
    return {**row, **overrides}


def test_global_day_uses_on_the_run_new_trades_only():
    rows = [
        trade(spread="0.0059"),
        trade(spread="0.0060"),
        trade(spread="0.0061"),
        trade(spread="0.0065", maturity="2031-06-20"),
        trade(spread="0.0090", **{"Action type": "MODI"}),
        trade(spread="0.0090", **{"Package indicator": "TRUE"}),
        trade(spread="0.0090", **{"Execution Timestamp": "2026-11-13T20:00:00Z"}),
        trade(spread="60", **{"Spread notation-Leg 1": "1"}),
        trade(name="CDX.NA.HY", spread="0.010575"),
        trade(name="CDX.NA.HY", spread="0.010577"),
        trade(name="CDX.NA.IG", spread="0.0090", **{"UPI FISN": "NA/CDS Idx Swt"}),
    ]
    day = credit_data.global_day(rows, "2026-11-16")
    assert day["CDX.NA.IG"] == {"value": 60.0, "trades": 3, "maturity": "2031-12-20"}
    assert day["CDX.NA.HY"] == {"value": 105.76, "trades": 2, "maturity": "2031-12-20"}
    assert "ITRAXX EUROPE" not in day


def test_summarize_reports_window_change_range_and_percentile():
    summary = credit_data.summarize([("2026-11-03", 30.0), ("2026-11-04", 28.0), ("2026-11-05", 33.0)])
    assert summary == {
        "start_date": "2026-11-03",
        "start": 30.0,
        "latest_date": "2026-11-05",
        "latest": 33.0,
        "change": 3.0,
        "min": 28.0,
        "max": 33.0,
        "percentile": 100.0,
        "observations": 3,
    }


def test_month_windows_stay_within_provider_limit():
    windows = credit_data.month_windows(date(2026, 9, 1), date(2026, 10, 20))
    assert windows == [
        (date(2026, 9, 1), date(2026, 9, 28)),
        (date(2026, 9, 29), date(2026, 10, 20)),
    ]
    # chinamoney rejects 2026-02-01..2026-03-02 (calendar month), so windows must fit February.
    assert credit_data.month_windows(date(2027, 2, 9), date(2027, 3, 10))[0] == (date(2027, 2, 9), date(2027, 3, 8))


def test_number_skips_non_finite_provider_values():
    assert credit_data.number("NaN") is None
    assert credit_data.number("inf") is None
    assert credit_data.number("46.94") == 46.94


CN_LATEST = {
    "records": [
        {
            "cnName": "CFETS-SHCH-GTHT高等级CDS指数",
            "enName": "CFETS-SHCH-GTHT High Grade CDS Index",
            "maxDate": "2026-11-17",
            "dataList": [{"date": "2026-11-17", "prd": "5Y", "cdIndxVl": "46.94"}],
        },
        {
            "cnName": "CFETS-SHCH民企CDS指数",
            "enName": "CFETS-SHCH Private Enterprise CDS Index",
            "maxDate": "2026-11-17",
            "dataList": [
                {"date": "2026-11-17", "prd": "3M", "cdIndxVl": "65.54"},
                {"date": "2026-11-17", "prd": "1Y", "cdIndxVl": "95.53"},
            ],
        },
    ]
}


def test_pick_prefers_exact_names_then_substrings():
    names = [(name, [name]) for name in credit_data.GLOBAL_INDICES]
    assert credit_data.pick(names, "") == list(credit_data.GLOBAL_INDICES)
    assert credit_data.pick(names, "itraxx europe") == ["ITRAXX EUROPE"]
    assert credit_data.pick(names, "hy") == ["CDX.NA.HY"]
    assert credit_data.pick(names, "crossover") == ["ITRAXX EUROPE CROSSOVER"]
    with pytest.raises(ValueError, match="available: CDX.NA.IG"):
        credit_data.pick(names, "EM")


def test_cn_filters_index_and_tenor_before_fetching_history(monkeypatch):
    history = [
        {"date": "2026-11-17", "prd": "3M", "cdIndxVl": "65.54"},
        {"date": "2026-11-16", "prd": "3M", "cdIndxVl": "64.00"},
        {"date": "2026-11-16", "prd": "1Y", "cdIndxVl": "95.00"},
    ]
    calls = []

    def post(url, params=None, **kwargs):
        calls.append(params)
        if params is None:
            return Response(CN_LATEST)
        page = history[(params["pageNum"] - 1) * 2 : params["pageNum"] * 2]
        return Response({"data": {"total": len(history), "minDate": "2025-11-17"}, "records": page})

    monkeypatch.setattr(credit_data, "today", lambda: date(2026, 11, 17))
    monkeypatch.setattr(credit_data, "PAGE_SIZE", 2)
    monkeypatch.setattr("requests.post", post)
    result = credit_data.cds_index(Namespace(source="cn", days=2, index="民企", tenor="3m"))
    [index] = result["indices"]
    assert index["name"] == "CFETS-SHCH民企CDS指数"
    assert index["curve"] == {"3M": 65.54}
    assert index["history"] == {"3M": credit_data.summarize([("2026-11-16", 64.0), ("2026-11-17", 65.54)])}
    assert [(call["indxName"], call["pageNum"]) for call in calls[1:]] == [
        ("CFETS-SHCH民企CDS指数", 1),
        ("CFETS-SHCH民企CDS指数", 2),
    ]
    assert calls[1]["startDate"] == "2026-11-16"
    assert result["unit"] == "bp"


def test_cn_skips_indices_without_requested_tenor_and_rejects_unknown_tenor(monkeypatch):
    monkeypatch.setattr(credit_data, "today", lambda: date(2026, 11, 17))
    monkeypatch.setattr(
        "requests.post", lambda url, params=None, **kwargs: Response(CN_LATEST if params is None else {})
    )
    result = credit_data.cds_index(Namespace(source="cn", days=2, index="", tenor="5Y"))
    assert [index["name"] for index in result["indices"]] == ["CFETS-SHCH-GTHT高等级CDS指数"]
    with pytest.raises(ValueError, match="tenor 10Y unavailable"):
        credit_data.cds_index(Namespace(source="cn", days=2, index="民企", tenor="10Y"))


def zipped(rows):
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("day.csv", text.getvalue())
    return buffer.getvalue()


def test_global_summary_only_compares_the_latest_series(monkeypatch):
    files = {
        "CFTC_CUMULATIVE_CREDITS_2026_11_16.zip": [trade(spread="0.0060")],
        "CFTC_CUMULATIVE_CREDITS_2026_11_13.zip": [
            trade(spread="0.0058", **{"Execution Timestamp": "2026-11-13T20:00:00Z"})
        ],
        "CFTC_CUMULATIVE_CREDITS_2026_09_18.zip": [
            trade(spread="0.0050", maturity="2031-06-20", **{"Execution Timestamp": "2026-09-18T20:00:00Z"})
        ],
        "CFTC_CUMULATIVE_CREDITS_2026_08_01.zip": [trade(spread="0.0040")],
    }
    listing = {"CFTC_CR": [{"fileName": name, "fullFilePath": f"https://bucket/{name}"} for name in files]}

    def get(url, **kwargs):
        if url.endswith("Cumulative.json"):
            return Response(listing)
        return Response(content=zipped(files[url.rsplit("/", 1)[1]]))

    monkeypatch.setattr("requests.get", get)
    result = credit_data.cds_index(Namespace(source="global", days=90, index="CDX.NA.IG", tenor=""))
    [ig] = result["indices"]
    assert ig["name"] == "CDX.NA.IG" and ig["quote_type"] == "spread_bp"
    assert ig["maturity"] == "2031-12-20"
    assert ig["summary"]["start"] == 58.0 and ig["summary"]["latest"] == 60.0
    assert result["files"] == 3
    with pytest.raises(ValueError, match="5Y"):
        credit_data.cds_index(Namespace(source="global", days=5, index="", tenor="1Y"))


def test_global_window_ends_at_newest_published_file(monkeypatch):
    names = ["CFTC_CUMULATIVE_CREDITS_2026_11_13.zip", "CFTC_CUMULATIVE_CREDITS_2026_11_15.zip"]
    listing = {"CFTC_CR": [{"fileName": name, "fullFilePath": f"https://bucket/{name}"} for name in names]}
    fetched = []

    def get(url, **kwargs):
        if url.endswith("Cumulative.json"):
            return Response(listing)
        fetched.append(url)
        return Response(content=zipped([trade(**{"Execution Timestamp": "2026-11-13T20:00:00Z"})]))

    monkeypatch.setattr("requests.get", get)
    result = credit_data.cds_index(Namespace(source="global", days=1, index="IG", tenor=""))
    # Sunday's file carries no index trades, so the window ends at Friday and skips the weekend download.
    assert result["start_date"] == result["end_date"] == "2026-11-13"
    assert fetched == ["https://bucket/CFTC_CUMULATIVE_CREDITS_2026_11_13.zip"]
    assert result["indices"][0]["summary"]["latest"] == 60.0


def test_unknown_index_error_keeps_the_query_case():
    with pytest.raises(ValueError, match="'CDX.NA.XX'"):
        credit_data.pick([(name, [name]) for name in credit_data.GLOBAL_INDICES], "CDX.NA.XX")


def test_cli_rejects_out_of_range_days_as_json_error():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools/credit_data.py"), "cds_index", "--days", "91"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout) == {"error": "days must be 1..90"}
