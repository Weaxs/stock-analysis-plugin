"""Offline tests for tools/macro_data.py (rates, FX, risk-appetite anchors)."""

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from tools import macro_data


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    import _cache

    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)


def test_macro_anchors_all_groups_and_derived_spreads(monkeypatch):
    idx = pd.to_datetime(["2026-11-20", "2026-11-21", "2026-11-24"])
    series_map = {
        "^TNX": [4.20, 4.25, 4.30],  # Yahoo ^TNX already returns percent-scale yields (e.g. 4.30 -> 4.30%)
        "DX-Y.NYB": [104.0, 104.5, 105.0],
        "CNH=X": [7.20, 7.22, 7.25],
        "HKD=X": [7.78, 7.78, 7.80],
        "^VIX": [16.0, 17.5, 18.0],
        "GC=F": [2650.0, 2680.0, 2700.0],
        "CL=F": [70.0, 71.0, 72.0],
    }

    def fake_yf_download(ticker, **kwargs):
        return pd.DataFrame({"Close": series_map[ticker]}, index=idx)

    import yfinance as yf

    monkeypatch.setattr(yf, "download", fake_yf_download)
    monkeypatch.setattr(
        macro_data,
        "_fetch_fred_series",
        lambda sid, days: [("2026-11-20", 3.90), ("2026-11-24", 4.00)] if sid == "DGS2" else [],
    )
    monkeypatch.setattr(
        macro_data,
        "_fetch_cn_us_yields_akshare",
        lambda days: {"cn_10y": [("2026-11-20", 2.10), ("2026-11-24", 2.15)]},
    )

    res = macro_data.get_macro_anchors("all", days=30)
    assert res["missing"] == []
    assert res["anchors"]["us_10y"]["latest"] == 4.3
    assert res["anchors"]["us_10y"]["unit"] == "percent"
    assert res["anchors"]["cn_10y"]["latest"] == 2.15
    assert res["anchors"]["us_2y"]["latest"] == 4.0
    assert res["derived"]["us_cn_10y_spread_bp"] == 215.0
    assert res["derived"]["us_10y_2y_spread_bp"] == 30.0
    assert res["anchors"]["hkdcny"]["latest"] == round(7.25 / 7.80, 4)
    assert res["anchors"]["vix"]["latest"] == 18.0


def test_us10y_falls_back_to_fred_when_yfinance_fails(monkeypatch):
    import yfinance as yf

    monkeypatch.setattr(yf, "download", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("yf down")))
    monkeypatch.setattr(
        macro_data,
        "_fetch_fred_series",
        lambda sid, days: [("2026-11-20", 4.10), ("2026-11-24", 4.20)] if sid == "DGS10" else [("2026-11-24", 3.9)],
    )
    monkeypatch.setattr(
        macro_data,
        "_fetch_cn_us_yields_akshare",
        lambda days: {"cn_10y": [("2026-11-20", 2.0), ("2026-11-24", 2.05)]},
    )

    res = macro_data.get_macro_anchors("rates", days=15)
    assert res["anchors"]["us_10y"]["source"] == "fred"
    assert res["anchors"]["us_10y"]["latest"] == 4.2
    assert res["meta"]["fallback_used"] is True


def test_all_failed_returns_error_and_invalid_inputs_rejected(monkeypatch):
    import yfinance as yf

    monkeypatch.setattr(yf, "download", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    res = macro_data.get_macro_anchors("risk", days=10)
    assert "error" in res

    assert "error" in macro_data.get_macro_anchors("invalid_group")
    assert "error" in macro_data.get_macro_anchors("all", days=1)
    assert "error" in macro_data.get_macro_anchors("all", days=30.5)


def test_cli_whole_float_days_accepted_and_invalid_days_exits_nonzero():
    script = Path(__file__).resolve().parents[1] / "tools" / "macro_data.py"
    proc = subprocess.run([sys.executable, str(script), "anchors", "--days", "1"], capture_output=True, text=True)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert "error" in payload
    assert macro_data._parse_int_days(30.0) == 30
    assert macro_data._parse_int_days("30.0") == 30
