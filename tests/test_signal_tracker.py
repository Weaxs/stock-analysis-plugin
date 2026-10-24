"""Tests for tools/signal_tracker.py — the JSONL signal outcome store."""

import json
import subprocess
import sys
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
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
    return {"date": date, "open": close, "high": high, "low": low, "close": close, "adjusted": "none"}


@pytest.fixture
def store(tmp_path):
    return tmp_path / "signals.jsonl"


@pytest.fixture
def mock_run_tool(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr(st, "run_tool", m)
    return m


class TestRecord:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"entry_price": 0},
            {"entry_price": float("nan")},
            {"target_price": -1},
            {"stop_price": float("inf")},
            {"horizon_days": 0},
            {"horizon_days": 1.5},
            {"date": "2026-02-30"},
            {"date": "2026-9-1"},
            {"direction": "hold"},
            {"source": {"bad": True}},
        ],
    )
    def test_invalid_signal_is_not_persisted(self, store, overrides):
        assert "error" in st.cmd_record(_rec_args(store, **overrides))
        assert not store.exists()

    def test_append_after_missing_final_newline(self, store):
        first = st.cmd_record(_rec_args(store))
        store.write_bytes(store.read_bytes().rstrip(b"\n"))
        second = st.cmd_record(_rec_args(store, symbol="000001"))
        summary = st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))
        assert {r["id"] for r in summary["records"]} == {first["id"], second["id"]}

    def test_record_refuses_corrupt_store(self, store):
        store.write_text('{"truncated":\n', encoding="utf-8")
        original = store.read_bytes()
        assert "error" in st.cmd_record(_rec_args(store))
        assert store.read_bytes() == original

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
            _bar("2026-05-21", 10, 10, 10),
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
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 10.2, 8.9, 9.1)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        r = result["results"][0]
        assert r["outcome"] == "stop_hit"
        assert r["return_pct"] == -10.0

    def test_same_day_target_and_stop_is_conservative_stop(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 12.5, 8.5, 11.0)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert result["results"][0]["outcome"] == "stop_hit"

    def test_timeout_profit(self, store, mock_run_tool):
        self._record(store, mock_run_tool, target_price=None, stop_price=None)
        mock_run_tool.return_value = [
            _bar("2026-09-01", 10, 10, 10),
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
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10)] + [
            _bar(f"2026-09-0{d}", 10.1, 9.4, 9.5) for d in (2, 3, 4)
        ]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert result["results"][0]["outcome"] == "timeout_loss"
        assert result["results"][0]["return_pct"] == -5.0

    def test_insufficient_bars_stays_open(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 10.5, 9.9, 10.2)]
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

    def test_history_missing_record_date_is_not_settled(self, store, mock_run_tool):
        self._record(store, mock_run_tool, date="2020-01-01", horizon_days=1)
        mock_run_tool.return_value = [_bar("2026-09-01", 21, 19, 20)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert result["results"][0]["outcome"] == "skipped_no_data"
        assert result["closed"] == 0
        assert st._load_records(store)[0]["status"] == "open"

    @pytest.mark.parametrize("bad", [None, float("nan"), "bad"])
    def test_incomplete_price_bar_does_not_record_a_false_win(self, store, mock_run_tool, bad):
        self._record(store, mock_run_tool, horizon_days=1)
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 12.5, bad, 12.1)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert result["closed"] == 0
        assert result["results"][0]["outcome"] == "skipped_no_data"
        assert st._load_records(store)[0]["status"] == "open"

    def test_sell_mirror(self, store, mock_run_tool):
        self._record(store, mock_run_tool, direction="sell", target_price=9.0, stop_price=11.0)
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 10.2, 8.9, 9.1)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        r = result["results"][0]
        assert r["outcome"] == "target_hit"
        assert r["return_pct"] == 10.0

    def test_sell_stop_hit(self, store, mock_run_tool):
        self._record(store, mock_run_tool, direction="sell", target_price=9.0, stop_price=11.0)
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 11.1, 9.9, 10.8)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        r = result["results"][0]
        assert r["outcome"] == "stop_hit"
        assert r["return_pct"] == -10.0

    def test_symbol_filter(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        self._record(store, mock_run_tool, symbol="000001")
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 12.5, 10.0, 12.1)]
        result = st.cmd_evaluate(Namespace(symbol="000001", store=str(store)))
        assert result["evaluated"] == 1
        assert result["results"][0]["symbol"] == "000001"

    def test_atomic_write_leaves_no_tmp(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 12.5, 10.0, 12.1)]
        st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert store.exists()
        assert not (store.parent / (store.name + ".tmp")).exists()

    def test_record_during_evaluation_is_preserved(self, store, mock_run_tool):
        original = self._record(store, mock_run_tool)
        added = []

        def fetch(*args, **kwargs):
            added.append(st.cmd_record(_rec_args(store, symbol="000001")))
            return [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 12.5, 10.0, 12.1)]

        mock_run_tool.side_effect = fetch
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        summary = st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))
        assert result["closed"] == 1
        assert {r["id"] for r in summary["records"]} == {original["id"], added[0]["id"]}
        assert summary["summary"]["open"] == 1

    @pytest.mark.parametrize("outer_bars", [None, [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 12.5, 10, 12)]])
    def test_overlapping_evaluators_return_the_persisted_outcome(self, store, mock_run_tool, outer_bars):
        self._record(store, mock_run_tool)
        calls = 0

        def fetch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
                return outer_bars
            return [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 10, 8.5, 9)]

        mock_run_tool.side_effect = fetch
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        summary = st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))
        assert result["results"][0]["outcome"] == summary["records"][0]["outcome"] == "stop_hit"
        assert result["results"][0]["return_pct"] == -10
        assert result["closed"] == 1
        assert result["still_open"] == 0

    def test_corrupt_store_is_not_silently_rewritten(self, store, mock_run_tool):
        self._record(store, mock_run_tool)
        with store.open("a", encoding="utf-8") as stream:
            stream.write('{"truncated":\n')
        original = store.read_bytes()
        mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 12.5, 10.0, 12.1)]
        result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert "error" in result
        assert store.read_bytes() == original

    def test_incomplete_signal_is_clean_error(self, store, mock_run_tool):
        store.write_text('{"id":"x","status":"open"}\n', encoding="utf-8")
        original = store.read_bytes()
        assert "error" in st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
        assert store.read_bytes() == original


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
            [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 12.5, 10.0, 12.1)],
            [_bar("2026-09-01", 10, 10, 10)] + [_bar(f"2026-09-0{d}", 10.1, 9.5, 9.0) for d in (2, 3, 4)],
            [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 10.5, 9.9, 10.2)],
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
        assert result["by_source"]["dragon-head"] == {
            "closed": 2,
            "wins": 1,
            "win_rate": 50.0,
            "avg_return_pct": 5.0,
            "return_samples": 2,
            "sample_warning": "Small observational sample; not strategy validation",
        }

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


def test_concurrent_cli_records_are_complete(store):
    script = Path(__file__).resolve().parents[1] / "tools" / "signal_tracker.py"

    def record(index):
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "record",
                "AAPL",
                "--direction",
                "buy",
                "--entry-price",
                "10",
                "--note",
                "信号" * 2000 + str(index),
                "--store",
                str(store),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with ThreadPoolExecutor(max_workers=6) as pool:
        written = list(pool.map(record, range(6)))
    summary = st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))
    assert summary["summary"]["total"] == 6
    assert {r["id"]: r["note"] for r in summary["records"]} == {r["id"]: r["note"] for r in written}


@pytest.mark.parametrize("missing_return", [None, float("nan"), True])
def test_source_summary_reports_return_sample_without_inventing_missing_returns(tmp_path, missing_return):
    from argparse import Namespace

    from tools import signal_tracker

    store = tmp_path / "signals.jsonl"
    rows = []
    for idx, ret in enumerate((10, -4, missing_return)):
        rows.append(
            {
                "id": str(idx),
                "symbol": "AAPL",
                "record_date": "2024-01-02",
                "status": "closed",
                "direction": "buy",
                "entry_price": 100,
                "horizon_days": 10,
                "source": "strategy-v1",
                "outcome": "timeout_profit" if idx == 0 else "timeout_loss",
                "return_pct": ret,
            }
        )
    store.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    result = signal_tracker.cmd_summary(Namespace(store=str(store), source=None, symbol=None, status="all"))
    group = result["by_source"]["strategy-v1"]
    assert result["summary"]["avg_return_pct"] == 3
    assert group["avg_return_pct"] == 3
    assert group["return_samples"] == 2
    assert group["sample_warning"]


@pytest.mark.parametrize("basis", ["qfq", "total_return", "unknown", None])
def test_unverified_price_basis_never_closes_original_price_signal(store, mock_run_tool, basis):
    st.cmd_record(_rec_args(store, entry_price=100, stop_price=90, target_price=120))
    bars = [_bar("2026-09-01", 50, 50, 50), _bar("2026-09-02", 51, 50, 51)]
    for bar in bars:
        if basis is None:
            bar.pop("adjusted", None)
        else:
            bar["adjusted"] = basis
    mock_run_tool.return_value = bars
    result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
    assert result["closed"] == 0
    assert result["results"][0]["outcome"] == "skipped_price_basis"
    assert (
        st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))["records"][0]["status"]
        == "open"
    )


def test_verified_adjustment_factor_keeps_split_return_correct(store, mock_run_tool):
    st.cmd_record(_rec_args(store, entry_price=100, stop_price=90, target_price=120, horizon_days=1))
    bars = [_bar("2026-09-01", 50, 50, 50), _bar("2026-09-02", 51, 50, 51)]
    for bar, factor in zip(bars, [0.5, 1]):
        bar.update(adjusted="qfq", adjustment_factor=factor)
    mock_run_tool.return_value = bars
    result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
    assert result["results"][0]["outcome"] == "timeout_profit"
    assert result["results"][0]["return_pct"] == 2
    saved = st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))["records"][0]
    assert saved["entry_price"] == 100
    assert saved["exit_price"] == 102
    assert saved["evaluation_price_basis"]["factor"] == 0.5


def test_weekend_quote_settles_against_actual_price_date(store, mock_run_tool):
    mock_run_tool.return_value = {"price": 100, "data_as_of": "2026-09-04"}
    rec = st.cmd_record(
        _rec_args(store, date="2026-09-05", entry_price=None, stop_price=90, target_price=120, horizon_days=1)
    )
    assert rec["price_as_of"] == "2026-09-04"
    bars = [_bar("2026-09-04", 50, 50, 50), _bar("2026-09-07", 51, 50, 51)]
    for bar, factor in zip(bars, [0.5, 1]):
        bar.update(adjusted="qfq", adjustment_factor=factor)
    mock_run_tool.return_value = bars

    result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))

    assert result["results"][0]["outcome"] == "timeout_profit"
    assert result["results"][0]["return_pct"] == 2
    saved = st._load_records(store)[0]
    assert saved["exit_price"] == 102
    assert saved["evaluation_price_basis"]["factor"] == 0.5


def test_legacy_signal_does_not_guess_factor_on_ex_date(store, mock_run_tool):
    st.cmd_record(_rec_args(store, date="2026-09-01", entry_price=100, stop_price=90, target_price=120, horizon_days=1))
    bars = [
        _bar("2026-08-31", 100, 100, 100),
        _bar("2026-09-01", 50, 50, 50),
        _bar("2026-09-02", 51, 50, 51),
    ]
    for bar, factor in zip(bars, [1, 0.5, 0.5]):
        bar.update(adjusted="qfq", adjustment_factor=factor)
    mock_run_tool.return_value = bars

    result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))

    assert result["closed"] == 0
    assert result["results"][0]["outcome"] == "skipped_price_basis"
    assert st._load_records(store)[0]["status"] == "open"


def test_non_trading_day_uses_prior_bar_when_factor_is_unchanged(store, mock_run_tool):
    st.cmd_record(_rec_args(store, date="2026-09-05", entry_price=100, stop_price=90, target_price=120, horizon_days=1))
    bars = [_bar("2026-09-04", 100, 100, 100), _bar("2026-09-07", 101, 100, 101)]
    for bar in bars:
        bar.update(adjusted="qfq", adjustment_factor=1)
    mock_run_tool.return_value = bars

    result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))

    assert result["results"][0]["outcome"] == "timeout_profit"
    assert result["results"][0]["return_pct"] == 1
    assert st._load_records(store)[0]["evaluation_price_basis"]["factor"] == 1


def test_future_quote_date_is_rejected_before_recording(store, mock_run_tool):
    mock_run_tool.return_value = {"price": 100, "data_as_of": "2026-09-07"}
    result = st.cmd_record(_rec_args(store, date="2026-09-01", entry_price=None))
    assert "error" in result
    assert not store.exists()


def test_legacy_future_quote_date_is_rejected_during_evaluation(store, mock_run_tool):
    rec = st.cmd_record(_rec_args(store, date="2026-09-01", entry_price=100, horizon_days=1))
    rec["price_as_of"] = "2026-09-07"
    store.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    bars = [
        _bar("2026-09-01", 50, 50, 50),
        _bar("2026-09-07", 100, 100, 100),
        _bar("2026-09-08", 102, 100, 102),
    ]
    for bar, factor in zip(bars, [0.5, 1, 1]):
        bar.update(adjusted="qfq", adjustment_factor=factor)
    mock_run_tool.return_value = bars

    result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))

    assert result["closed"] == 0
    assert result["results"][0]["outcome"] == "skipped_price_basis"


def test_iso_quote_timestamp_uses_market_local_date(store, mock_run_tool):
    mock_run_tool.return_value = {"price": 100, "data_as_of": "2026-09-04T20:00:00+00:00"}
    rec = st.cmd_record(
        _rec_args(
            store,
            symbol="AAPL",
            date="2026-09-04",
            entry_price=None,
            stop_price=90,
            target_price=120,
            horizon_days=1,
        )
    )
    assert rec["price_date"] == "2026-09-04"
    bars = [
        _bar("2026-09-03", 100, 100, 100),
        _bar("2026-09-04", 50, 50, 50),
        _bar("2026-09-07", 51, 50, 51),
    ]
    for bar, factor in zip(bars, [1, 0.5, 0.5]):
        bar.update(adjusted="qfq", adjustment_factor=factor)
    mock_run_tool.return_value = bars

    result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))

    assert result["results"][0]["outcome"] == "timeout_profit"
    assert result["results"][0]["return_pct"] == 2


def test_batch_deadline_returns_and_persists_completed_results(store, mock_run_tool, monkeypatch):
    for symbol in ["600519", "000001", "300001", "600000", "000002"]:
        st.cmd_record(_rec_args(store, symbol=symbol))
    elapsed = 0
    monkeypatch.setattr(st.time, "monotonic", lambda: elapsed)

    def fetch(*args, timeout, **kwargs):
        nonlocal elapsed
        elapsed += min(80, timeout)
        if timeout < 80:
            return None
        return [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 13, 10, 12)]

    mock_run_tool.side_effect = fetch
    result = st.cmd_evaluate(Namespace(symbol=None, store=str(store)))
    assert elapsed == 270
    assert result["closed"] == 3
    assert result["deferred"] == 1
    assert result["still_open"] == 2
    assert result["complete"] is False
    assert result["warnings"]
    summary = st.cmd_summary(Namespace(source=None, symbol=None, status="all", store=str(store)))
    assert summary["summary"]["closed"] == 3
    assert summary["summary"]["open"] == 2


def test_deadline_retry_reaches_signals_after_slow_failures(store, mock_run_tool, monkeypatch):
    for symbol in ("BAD1", "BAD2", "BAD3", "AAPL"):
        st.cmd_record(_rec_args(store, symbol=symbol))
    elapsed = 0
    calls = []
    monkeypatch.setattr(st.time, "monotonic", lambda: elapsed)

    def fetch(script, args, timeout, **kwargs):
        nonlocal elapsed
        calls.append(args[1])
        if args[1].startswith("BAD"):
            elapsed += timeout
            return None
        return [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 13, 10, 12)]

    mock_run_tool.side_effect = fetch
    args = Namespace(symbol=None, store=str(store))
    first = st.cmd_evaluate(args)
    assert first["deferred"] == 1
    assert first["closed"] == 0
    assert calls == ["BAD1", "BAD2", "BAD3"]
    calls.clear()
    second = st.cmd_evaluate(args)
    assert calls[0] == "AAPL"
    assert second["closed"] == 1
    saved = {rec["symbol"]: rec for rec in st._load_records(store, strict=True)}
    assert set(saved) == {"BAD1", "BAD2", "BAD3", "AAPL"}
    assert saved["AAPL"]["status"] == "closed"
    assert saved["AAPL"]["outcome"] == "target_hit"
    assert all(saved[symbol]["status"] == "open" for symbol in ("BAD1", "BAD2", "BAD3"))


def test_retry_queue_preserves_closed_filtered_and_concurrently_added_records(store, mock_run_tool, monkeypatch):
    signals = [st.cmd_record(_rec_args(store, symbol="AAPL", note=f"signal-{i}")) for i in range(4)]
    other = st.cmd_record(_rec_args(store, symbol="MSFT"))
    closed = st.cmd_record(_rec_args(store, symbol="GOOG"))
    mock_run_tool.return_value = [_bar("2026-09-01", 10, 10, 10), _bar("2026-09-02", 13, 10, 12)]
    st.cmd_evaluate(Namespace(symbol="GOOG", store=str(store)))
    before = st._load_records(store, strict=True)
    elapsed = 0
    added = []
    monkeypatch.setattr(st.time, "monotonic", lambda: elapsed)

    def fetch(script, args, timeout, **kwargs):
        nonlocal elapsed
        if not added:
            added.append(st.cmd_record(_rec_args(store, symbol="TSLA")))
        elapsed += timeout
        return None

    mock_run_tool.side_effect = fetch
    result = st.cmd_evaluate(Namespace(symbol="AAPL", store=str(store)))
    assert result["closed"] == 0
    assert result["deferred"] == 1
    saved = st._load_records(store, strict=True)
    assert [rec["id"] for rec in saved[:4]] == [signals[3]["id"], *(rec["id"] for rec in signals[:3])]
    assert saved[4] == other
    assert saved[5] == before[5]
    assert saved[5]["id"] == closed["id"]
    assert saved[5]["status"] == "closed"
    assert saved[6] == added[0]
    assert {rec["id"] for rec in saved} == {rec["id"] for rec in [*before, *added]}
