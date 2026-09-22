import json
import sys
from argparse import Namespace
from unittest.mock import patch

import pytest

from tools import market_review as mr
from tools.market_review import _signal_to_stance, calc_temperature, review_market


class TestCalcTemperature:
    def test_all_up_market(self):
        stats = {"up_count": 4000, "down_count": 500, "limit_up_count": 80, "limit_down_count": 5}
        indices = [{"change_pct": 2.5}, {"change_pct": 3.0}, {"change_pct": 1.8}]
        result = calc_temperature(stats, indices)
        assert result["signal"] == "green"
        assert result["score"] > 60

    def test_all_down_market(self):
        stats = {"up_count": 500, "down_count": 4000, "limit_up_count": 5, "limit_down_count": 80}
        indices = [{"change_pct": -2.5}, {"change_pct": -3.0}, {"change_pct": -1.8}]
        result = calc_temperature(stats, indices)
        assert result["signal"] == "red"
        assert result["score"] < 40

    def test_neutral_market(self):
        stats = {"up_count": 2000, "down_count": 2000, "limit_up_count": 20, "limit_down_count": 20}
        indices = [{"change_pct": 0.1}, {"change_pct": -0.1}]
        result = calc_temperature(stats, indices)
        assert result["signal"] == "yellow"
        assert 40 <= result["score"] <= 60

    def test_no_data(self):
        result = calc_temperature(None, None)
        assert result["score"] == 50
        assert result["signal"] == "yellow"

    def test_only_indices(self):
        indices = [{"change_pct": 3.0}]
        result = calc_temperature(None, indices)
        assert result["score"] > 50

    def test_only_stats(self):
        stats = {"up_count": 3000, "down_count": 1000, "limit_up_count": 50, "limit_down_count": 10}
        result = calc_temperature(stats, None)
        assert result["score"] > 50

    def test_index_score_clamped(self):
        indices = [{"change_pct": 10.0}]
        result = calc_temperature(None, indices)
        assert result["score"] <= 100

    def test_index_score_clamped_negative(self):
        indices = [{"change_pct": -10.0}]
        result = calc_temperature(None, indices)
        assert result["score"] >= 0

    def test_empty_stats(self):
        stats = {"up_count": 0, "down_count": 0, "limit_up_count": 0, "limit_down_count": 0}
        indices = [{"change_pct": 1.0}]
        result = calc_temperature(stats, indices)
        assert "score" in result


class TestSignalToStance:
    def test_green(self):
        assert _signal_to_stance("green") == "offensive"

    def test_yellow(self):
        assert _signal_to_stance("yellow") == "balanced"

    def test_red(self):
        assert _signal_to_stance("red") == "defensive"

    def test_unknown(self):
        assert _signal_to_stance("unknown") == "balanced"


class TestReviewMarket:
    @patch("tools.market_review._run_tool")
    def test_a_market_structure(self, mock_run):
        mock_run.return_value = None
        result = review_market("A")
        assert result["market"] == "A"
        assert "date" in result
        assert "temperature" in result
        assert "strategy_stance" in result
        assert result["temperature"]["signal"] == "yellow"

    @patch("tools.market_review._run_tool")
    def test_hk_market(self, mock_run):
        mock_run.return_value = None
        result = review_market("HK")
        assert result["market"] == "HK"
        assert "indices" in result

    @patch("tools.market_review._run_tool")
    def test_us_market(self, mock_run):
        mock_run.return_value = None
        result = review_market("US")
        assert result["market"] == "US"

    @patch("tools.market_review._run_tool")
    def test_unknown_market(self, mock_run):
        result = review_market("XX")
        assert "error" in result

    @patch("tools.market_review._run_tool")
    def test_with_real_data(self, mock_run):
        def side_effect(script, args, timeout=30):
            if "market_stats" in args:
                return {"up_count": 3000, "down_count": 1500, "limit_up_count": 40, "limit_down_count": 8}
            if "market_indices" in args:
                return [{"change_pct": 1.2}, {"change_pct": 0.8}]
            return None

        mock_run.side_effect = side_effect
        result = review_market("A")
        assert result["temperature"]["signal"] == "green"
        assert result["strategy_stance"] == "offensive"

    @patch("tools.market_review._run_tool")
    def test_market_stats_gets_snapshot_budget(self, mock_run):
        """market_stats fans out to a full-market snapshot — it must run with the
        same 240s budget screener.fetch_snapshot gives that path, or a weak network
        silently degrades the temperature to the neutral-50 fallback."""
        mock_run.return_value = None
        review_market("A")
        stats_call = [c for c in mock_run.call_args_list if c[0][1] == ["market_stats"]]
        assert len(stats_call) == 1
        assert stats_call[0][0][2] == 240  # positional timeout arg
        # the other legs keep the run_tool default
        news_call = [c for c in mock_run.call_args_list if c[0][0] == "search_intel.py"]
        assert news_call[0][0][2] == 30


class TestReviewArchive:
    """The JSONL review archive: render_market_report --save writes, history reads."""

    def test_save_review_appends_jsonl(self, tmp_path):
        store = tmp_path / "reviews.jsonl"
        r1 = mr.save_review({"market": "A", "review_date": "2026-09-21", "temperature": {"score": 55}}, store)
        r2 = mr.save_review({"market": "A", "review_date": "2026-09-22", "temperature": {"score": 61}}, store)
        assert r1["saved"] is True and r2["saved"] is True
        assert r2["date"] == "2026-09-22"
        records = mr._load_records(store)
        assert [r["review_date"] for r in records] == ["2026-09-21", "2026-09-22"]
        # one JSON object per line
        lines = store.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["market"] == "A"

    def test_save_review_creates_parent_dirs(self, tmp_path):
        store = tmp_path / "nested" / "dir" / "reviews.jsonl"
        result = mr.save_review({"market": "HK", "review_date": "2026-09-22"}, store)
        assert result["saved"] is True
        assert store.exists()

    def test_save_review_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STOCK_REVIEW_STORE", str(tmp_path / "env.jsonl"))
        result = mr.save_review({"market": "US", "review_date": "2026-09-22"})
        assert result["saved"] is True
        assert result["date"] == "2026-09-22"
        # the store path (an absolute, OS-username-bearing path) is never echoed to stdout
        assert "store" not in result
        assert mr._load_records(tmp_path / "env.jsonl")[0]["market"] == "US"

    def test_history_missing_store_is_empty(self, tmp_path):
        result = mr.cmd_history(Namespace(limit=10, store=str(tmp_path / "nope.jsonl")))
        assert result == {"count": 0, "records": []}

    def test_history_newest_first_with_limit(self, tmp_path):
        store = tmp_path / "reviews.jsonl"
        for i in range(5):
            mr.save_review({"market": "A", "review_date": f"2026-09-1{i}"}, store)
        result = mr.cmd_history(Namespace(limit=3, store=str(store)))
        assert result["count"] == 3
        assert [r["review_date"] for r in result["records"]] == ["2026-09-14", "2026-09-13", "2026-09-12"]

    def test_history_default_limit_10(self, tmp_path):
        store = tmp_path / "reviews.jsonl"
        for i in range(15):
            mr.save_review({"market": "A", "review_date": f"2026-09-{i + 1:02d}"}, store)
        result = mr.cmd_history(Namespace(limit=10, store=str(store)))
        assert result["count"] == 10
        assert result["records"][0]["review_date"] == "2026-09-15"
        assert result["records"][-1]["review_date"] == "2026-09-06"

    def test_history_limit_zero(self, tmp_path):
        store = tmp_path / "reviews.jsonl"
        mr.save_review({"market": "A", "review_date": "2026-09-22"}, store)
        result = mr.cmd_history(Namespace(limit=0, store=str(store)))
        assert result == {"count": 0, "records": []}

    def test_history_skips_corrupt_lines(self, tmp_path):
        store = tmp_path / "reviews.jsonl"
        store.write_text(
            '{"market": "A", "review_date": "2026-09-21"}\nnot-json\n{"market": "HK", "review_date": "2026-09-22"}\n',
            encoding="utf-8",
        )
        result = mr.cmd_history(Namespace(limit=10, store=str(store)))
        assert result["count"] == 2
        assert result["records"][0]["review_date"] == "2026-09-22"

    def test_history_store_is_directory_is_clean_error(self, tmp_path):
        # read_text() on a directory raises IsADirectoryError (OSError) — must not
        # escape as a traceback (hermes _run would relay it raw into {"error": ...})
        result = mr.cmd_history(Namespace(limit=10, store=str(tmp_path)))
        assert isinstance(result, dict)
        assert "error" in result

    def test_history_store_not_utf8_is_clean_error(self, tmp_path):
        store = tmp_path / "reviews.jsonl"
        store.write_bytes(b'{"market": "A"}\n\xff\xfe invalid\n')
        result = mr.cmd_history(Namespace(limit=10, store=str(store)))
        assert isinstance(result, dict)
        assert "error" in result

    def test_history_via_cli(self, tmp_path, capsys):
        store = tmp_path / "reviews.jsonl"
        mr.save_review({"market": "A", "review_date": "2026-09-22"}, store)
        argv = ["market_review.py", "history", "--limit", "5", "--store", str(store)]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", argv)
            mr.main()
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 1
        assert out["records"][0]["market"] == "A"
