import json
import subprocess

import pytest

from tests.e2e.test_provider_network import TestScreenerNetwork as ScreenerChecks
from tests.e2e.test_provider_network import _run_cli


def test_snapshot_failure_reaches_skip(monkeypatch):
    payload = {"error": "US snapshot unavailable: connection closed", "market": "US"}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 1, json.dumps(payload), ""))
    out = _run_cli("screener.py", ["screen", "--market", "US"])
    with pytest.raises(pytest.skip.Exception):
        ScreenerChecks()._skip_if_snapshot_down(out, "US")


@pytest.mark.parametrize("error", ["Unsupported filter: typo", "US snapshot unavailable: connection closed"])
def test_other_cli_failures_stay_failures(monkeypatch, error):
    payload = {"error": error, "market": "US"}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 1, json.dumps(payload), ""))
    with pytest.raises(AssertionError):
        _run_cli("stock_data.py", ["quote", "AAPL"])


def test_screener_validation_failure_stays_failure(monkeypatch):
    payload = {"error": "Unsupported filter: typo", "market": "US"}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 1, json.dumps(payload), ""))
    with pytest.raises(AssertionError):
        _run_cli("screener.py", ["screen", "--market", "US"])


def test_l2_network_probe_allows_snapshot_sentiment_and_enrichment(monkeypatch):
    def run(cmd, **kwargs):
        # Snapshot (240s), sentiment (120s), one enrichment wave (360s), plus overhead.
        assert kwargs["timeout"] > 720
        payload = {"error": "A-share snapshot unavailable: connection closed", "market": "A"}
        return subprocess.CompletedProcess(cmd, 1, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(pytest.skip.Exception):
        ScreenerChecks().test_screen_a_l2_enrichment()
