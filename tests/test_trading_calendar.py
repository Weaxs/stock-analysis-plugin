from datetime import datetime

import pytest

from tools.trading_calendar import _is_weekend


class TestIsWeekend:
    def test_monday(self):
        assert _is_weekend(datetime(2024, 1, 1)) is False  # Monday

    def test_friday(self):
        assert _is_weekend(datetime(2024, 1, 5)) is False  # Friday

    def test_saturday(self):
        assert _is_weekend(datetime(2024, 1, 6)) is True  # Saturday

    def test_sunday(self):
        assert _is_weekend(datetime(2024, 1, 7)) is True  # Sunday

    def test_wednesday(self):
        assert _is_weekend(datetime(2024, 7, 10)) is False  # Wednesday


from tools.trading_calendar import is_trading_day  # noqa: E402


class TestNewMarkets:
    def test_jp_weekend_not_trading(self):
        r = is_trading_day("JP", "2024-01-06")  # Saturday
        assert r["is_trading_day"] is False
        assert r["reason"] == "weekend"

    def test_kr_weekday_is_trading(self):
        # 2024-07-10 Wednesday, no JP/KR/TW holiday — true via calendar lib or fallback
        r = is_trading_day("KR", "2024-07-10")
        assert r["is_trading_day"] is True

    def test_tw_weekday_is_trading(self):
        r = is_trading_day("TW", "2024-07-10")
        assert r["is_trading_day"] is True

    def test_jp_uses_exchange_calendar_when_available(self):
        pytest.importorskip("exchange_calendars")
        r = is_trading_day("JP", "2024-01-01")  # New Year's Day, TSE closed
        assert r["is_trading_day"] is False
        assert r["source"] == "exchange-calendars"

    def test_unknown_market_error(self):
        r = is_trading_day("XX", "2024-07-10")
        assert "error" in r


from tools import trading_calendar as tc  # noqa: E402


@pytest.fixture
def trading_day_on(monkeypatch):
    """Deterministic calendar: every weekday is a trading day."""
    monkeypatch.setattr(tc, "is_trading_day", lambda market, date_str=None: {"is_trading_day": True})


@pytest.fixture
def trading_day_off(monkeypatch):
    monkeypatch.setattr(tc, "is_trading_day", lambda market, date_str=None: {"is_trading_day": False})


class TestMarketPhase:
    def test_cn_pre_market(self, trading_day_on):
        r = tc.market_phase("CN", "2026-09-18T09:00:00")
        assert r["phase"] == "pre_market"
        assert r["phase_label"] == "盘前"

    def test_cn_morning(self, trading_day_on):
        r = tc.market_phase("CN", "2026-09-18T10:30:00")
        assert r["phase"] == "morning"
        assert r["phase_label"] == "早盘"
        assert r["timezone"] == "Asia/Shanghai"
        assert r["date"] == "2026-09-18"
        assert r["local_time"] == "2026-09-18T10:30:00"
        assert r["is_trading_day"] is True
        assert r["sessions"] == [("09:30", "11:30"), ("13:00", "15:00")]

    def test_cn_lunch_break(self, trading_day_on):
        assert tc.market_phase("CN", "2026-09-18T12:00:00")["phase"] == "lunch_break"

    def test_cn_afternoon(self, trading_day_on):
        assert tc.market_phase("CN", "2026-09-18T14:00:00")["phase"] == "afternoon"

    def test_cn_post_market(self, trading_day_on):
        assert tc.market_phase("CN", "2026-09-18T15:30:00")["phase"] == "post_market"

    def test_hk_lunch_break(self, trading_day_on):
        r = tc.market_phase("HK", "2026-09-18T12:30:00")
        assert r["phase"] == "lunch_break"
        assert r["timezone"] == "Asia/Hong_Kong"

    def test_us_intraday_no_lunch(self, trading_day_on):
        # noon is still trading — US sessions have no lunch break
        assert tc.market_phase("US", "2026-09-18T12:00:00")["phase"] == "intraday"
        assert tc.market_phase("US", "2026-09-18T09:00:00")["phase"] == "pre_market"
        assert tc.market_phase("US", "2026-09-18T17:00:00")["phase"] == "post_market"
        r = tc.market_phase("US", "2026-09-18T10:30:00")
        assert r["sessions"] == [("09:30", "16:00")]
        assert r["timezone"] == "America/New_York"

    def test_jp_lunch_break(self, trading_day_on):
        assert tc.market_phase("JP", "2026-09-18T12:00:00")["phase"] == "lunch_break"
        # XTKS closes at 15:30 (TSE extended its close from 15:00 in 2024-11)
        assert tc.market_phase("JP", "2026-09-18T15:30:00")["phase"] == "afternoon"
        assert tc.market_phase("JP", "2026-09-18T15:31:00")["phase"] == "post_market"

    def test_kr_intraday_no_lunch(self, trading_day_on):
        assert tc.market_phase("KR", "2026-09-18T12:00:00")["phase"] == "intraday"
        assert tc.market_phase("KR", "2026-09-18T16:00:00")["phase"] == "post_market"

    def test_tw_single_session_intraday(self, trading_day_on):
        r = tc.market_phase("TW", "2026-09-18T10:00:00")
        assert r["phase"] == "intraday"
        assert r["sessions"] == [("09:00", "13:30")]

    def test_non_trading_day_is_closed(self, trading_day_off):
        r = tc.market_phase("CN", "2026-09-18T10:30:00")
        assert r["phase"] == "closed"
        assert r["phase_label"] == "休市"
        assert r["is_trading_day"] is False

    def test_lowercase_market(self, trading_day_on):
        assert tc.market_phase("cn", "2026-09-18T10:30:00")["market"] == "CN"

    def test_unknown_market(self):
        assert "error" in tc.market_phase("XX", "2026-09-18T10:30:00")

    def test_invalid_at(self):
        assert "error" in tc.market_phase("CN", "not-a-time")


import sys  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pandas as pd  # noqa: E402


class TestCnTradeDatesMemo:
    """cn_trade_dates is memoized per process (the stale-date walk-back would
    otherwise re-fetch the sina calendar per step); failures are never cached."""

    def test_memoized_and_failures_retry(self, monkeypatch):
        monkeypatch.setattr(tc, "_CN_TRADE_DATES", {})
        mock_ak = MagicMock()
        mock_ak.tool_trade_date_hist_sina.return_value = pd.DataFrame(
            {"trade_date": ["2026-09-18", "2026-09-17", "2025-12-31"]}
        )
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            assert tc.cn_trade_dates(2026) == {"2026-09-18", "2026-09-17"}
            assert tc.cn_trade_dates(2026) == {"2026-09-18", "2026-09-17"}
            mock_ak.tool_trade_date_hist_sina.assert_called_once()
            mock_ak.tool_trade_date_hist_sina.side_effect = ConnectionError("boom")
            assert tc.cn_trade_dates(2027) == set()
            assert tc.cn_trade_dates(2027) == set()
            assert mock_ak.tool_trade_date_hist_sina.call_count == 3
