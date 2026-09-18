"""Tests for tools/signal_tracker.py — the JSONL signal outcome store."""

import json
from argparse import Namespace
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from tools import signal_tracker as st


def _rec_args(store, **over):
    base = {
        "symbol": "600519",
        "direction": "buy",
        "entry_price": 10.0,
        "target_price": 12.0,
        "stop_price": 9.0,
        "horizon_days": 3,
        "source": None,
        "note": None,
        "date": "2026-09-01",
        "store": str(store),
    }
    base.update(over)
    return Namespace(**base)


def _bar(date, high, low, close):
    return {"date": date, "open": close, "high": high, "low": low, "close": close}


@pytest.fixture
def store(tmp_path):
    return tmp_path / "signals.jsonl"


@pytest.fixture
def mock_run_tool(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr(st, "run_tool", m)
    return m


class TestRecord:
    def test_explicit_entry_price_no_quote_call(self, store, mock_run_tool):
        rec = st.cmd_record(_rec_args(store))
        assert rec["status"] == "open"
        assert rec["outcome"] is None
        assert rec["market"] == "A"
        assert rec["entry_price"] == 10.0
        assert rec["id"].startswith("2026-09-01-600519-")
        mock_run_tool.assert_not_called()
        loaded = st._load_records(store)
        assert loaded == [rec]

    def test_entry_price_from_quote(self, store, mock_run_tool):
        mock_run_tool.return_value = {"symbol": "600519", "price": 1800.0}
        rec = st.cmd_record(_rec_args(store, entry_price=None))
        assert rec["entry_price"] == 1800.0
        mock_run_tool.assert_called_once_with("stock_data.py", ["quote", "600519"])

    def test_quote_failure_is_error(self, store, mock_run_tool):
        mock_run_tool.return_value = {"error": "provider down"}
        result = st.cmd_record(_rec_args(store, entry_price=None))
        assert "error" in result
        assert not store.exists()


class TestEvaluate:
    @pytest.fixture(autouse=True)
    def _freeze_now(self, monkeypatch):
        """Pin datetime.now so _kline_count's elapsed-days computation is deterministic."""

        class _FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 18)

        monkeypatch.setattr(st, "datetime", _FrozenDateTime)

    def _record(self, store, mock_run_tool, **over):
        return st.cmd_record(_rec_args(store, **over))

    def test_target_hit(self, store, mock_run_tool):
        rec = self._record(store, mock_run_tool)
        mock_run_tool.return_value = [
            _bar("2026-09-01", 10.5, 9.8, 10.2),  # record_date itself: ignored
            _bar("2026-09-02", 11.0, 10.0, 10.8),
            _bar("2026-09-03", 12.1, 10.5, 11.9),
        ]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert result["closed"] == 1 and result["still_open"] == 0
        r = result["results"][0]
        assert r["id"] == rec["id"]
        assert r["outcome"] == "target_hit"
        assert r["exit_date"] == "2026-09-03"
        assert r["return_pct"] == 20.0
        saved = st._load_records(store)[0]
        assert saved["status"] == "closed"
        assert saved["exit_price"] == 12.0
        # kline fetch window includes the +40 buffer
        assert mock_run_tool.call_args[0][1] == ["kline", "600519", "--period", "daily", "--count", "43"]

    def test_old_signal_window_anchored_to_record_date(self, store, mock_run_tool):
        """An open signal recorded long ago must be evaluated against bars right
        after record_date, not against the most recent bars (window anchored at
        today would miss the whole evaluation window)."""
        self._record(store, mock_run_tool, date="2026-05-21", horizon_days=5)
        mock_run_tool.return_value = [
            _bar("2026-05-22", 12.5, 9.9, 12.1),  # target hit the day after recording
            _bar("2026-09-17", 8.0, 7.0, 7.5),  # stale deep-loss bar must not matter
        ]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        r = result["results"][0]
        assert r["outcome"] == "target_hit"
        assert r["exit_date"] == "2026-05-22"
        # 120 calendar days elapsed since record_date → count = 120 + 15 = 135
        assert mock_run_tool.call_args[0][1][-1] == "135"

    def test_stop_hit(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        mock_run_tool.return_value = [_bar("2026-09-02", 10.2, 8.9, 9.1)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        r = result["results"][0]
        assert r["outcome"] == "stop_hit"
        assert r["return_pct"] == -10.0

    def test_same_day_target_and_stop_is_conservative_stop(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        mock_run_tool.return_value = [_bar("2026-09-02", 12.5, 8.5, 11.0)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert result["results"][0]["outcome"] == "stop_hit"

    def test_timeout_profit(self, store, mock_run_tool):
        self._record(store, mock_run_tool, target_price=None, stop_price=None)
        mock_run_tool.return_value = [
            _bar("2026-09-02", 10.2, 9.9, 10.1),
            _bar("2026-09-03", 10.3, 10.0, 10.2),
            _bar("2026-09-04", 10.6, 10.1, 10.5),
        ]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        r = result["results"][0]
        assert r["outcome"] == "timeout_profit"
        assert r["return_pct"] == 5.0
        assert r["exit_date"] == "2026-09-04"

    def test_timeout_loss(self, store, mock_run_tool):
        self._record(store, mock_run_tool, target_price=None, stop_price=None)
        mock_run_tool.return_value = [_bar(f"2026-09-0{d}", 10.1, 9.4, 9.5) for d in (2, 3, 4)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert result["results"][0]["outcome"] == "timeout_loss"
        assert result["results"][0]["return_pct"] == -5.0

    def test_insufficient_bars_stays_open(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        mock_run_tool.return_value = [_bar("2026-09-02", 10.5, 9.9, 10.2)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert result["closed"] == 0 and result["still_open"] == 1
        assert result["results"][0]["outcome"] == "open"
        assert st._load_records(store)[0]["status"] == "open"

    def test_kline_failure_skipped_not_error(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        mock_run_tool.return_value = {"error": "provider down"}
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert "error" not in result
        assert result["results"][0]["outcome"] == "skipped_no_data"
        assert st._load_records(store)[0]["status"] == "open"

    def test_sell_mirror(self, store, mock_run_tool):
        self._record(store, mock_run_tool, direction="sell", target_price=9.0, stop_price=11.0)
        mock_run_tool.return_value = [_bar("2026-09-02", 10.2, 8.9, 9.1)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        r = result["results"][0]
        assert r["outcome"] == "target_hit"
        assert r["return_pct"] == 10.0

    def test_sell_stop_hit(self, store, mock_run_tool):
        self._record(store, mock_run_tool, direction="sell", target_price=9.0, stop_price=11.0)
        mock_run_tool.return_value = [_bar("2026-09-02", 11.1, 9.9, 10.8)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        r = result["results"][0]
        assert r["outcome"] == "stop_hit"
        assert r["return_pct"] == -10.0

    def test_symbol_filter(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        self._record(store, mock_run_tool, symbol="000001")
        mock_run_tool.return_value = [_bar("2026-09-02", 12.5, 10.0, 12.1)]
        result = st.cmd_evaluate(Namespace(symbol="000001", store=str(store)))
        assert result["evaluated"] == 1
        assert result["results"][0]["symbol"] == "000001"

    def test_atomic_write_leaves_no_tmp(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        mock_run_tool.return_value = [_bar("2026-09-02", 12.5, 10.0, 12.1)]
        st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert store.exists()
        assert not (store.parent / (store.name + ".tmp")).exists()


class TestSummary:
    def test_missing_store_is_empty_summary(self, store):
        result = st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))
        assert result["summary"]["total"] == 0
        assert result["summary"]["win_rate"] is None
        assert result["records"] == []

    def test_counts_win_rate_and_by_source(self, store, mock_run_tool):
        st.cmd_record(_rec_args(store, source="dragon-head"))
        st.cmd_record(_rec_args(store, source="dragon-head", symbol="000001"))
        st.cmd_record(_rec_args(store, source="hot-theme", symbol="300750"))
        # close #1 target_hit (+20), close #2 timeout_loss (-10), keep #3 open
        mock_run_tool.side_effect = [
            [_bar("2026-09-02", 12.5, 10.0, 12.1)],
            [_bar(f"2026-09-0{d}", 10.1, 9.5, 9.0) for d in (2, 3, 4)],
            [_bar("2026-09-02", 10.5, 9.9, 10.2)],
        ]
        st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        result = st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))
        s = result["summary"]
        assert s["total"] == 3
        assert s["open"] == 1
        assert s["closed"] == 2
        assert s["target_hit"] == 1
        assert s["timeout_loss"] == 1
        assert s["win_rate"] == 50.0
        assert s["avg_return_pct"] == 5.0
        assert result["by_source"]["dragon-head"] == {"closed": 2, "wins": 1, "win_rate": 50.0}

    def test_filters(self, store, mock_run_tool):
        st.cmd_record(_rec_args(store, source="a"))
        st.cmd_record(_rec_args(store, symbol="000001", source="b"))
        by_symbol = st.cmd_summary(Namespace(source=None, symbol="000001", status="all", store=str(store)))
        assert by_symbol["summary"]["total"] == 1
        assert by_symbol["filters"]["symbol"] == "000001"
        by_source = st.cmd_summary(Namespace(source="a", symbol=None, status="all", store=str(store)))
        assert by_source["summary"]["total"] == 1
        by_status = st.cmd_summary(Namespace(source=None, symbol=None, status="open", store=str(store)))
        assert by_status["summary"]["total"] == 2
        by_status = st.cmd_summary(Namespace(source=None, symbol=None, status="closed", store=str(store)))
        assert by_status["summary"]["total"] == 0


def test_jsonl_roundtrip_via_cli(store, mock_run_tool):
    """End-to-end through main(): record then summary, argv-driven."""
    import sys

    argv = [
        "signal_tracker.py",
        "record",
        "600519",
        "--direction",
        "buy",
        "--entry-price",
        "10",
        "--date",
        "2026-09-01",
        "--store",
        str(store),
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", argv)
        st.main()
    assert len(st._load_records(store)) == 1


def test_main_error_exits_nonzero(store, mock_run_tool, capsys):
    import sys

    mock_run_tool.return_value = None  # quote unavailable
    argv = ["signal_tracker.py", "record", "600519", "--direction", "buy", "--store", str(store)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exc:
            st.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out
