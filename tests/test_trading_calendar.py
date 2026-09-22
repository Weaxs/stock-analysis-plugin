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


class TestCnTradeDatesTimeout:
    """cn_trade_dates' akshare fetch is timeout-less by default — the quote stale-marker
    path calls it on every quote, so the fetch must run under a bounded socket timeout
    that is always restored."""

    def test_fetch_bounded_and_restored(self, monkeypatch, record_socket_timeout):
        seen = record_socket_timeout()
        monkeypatch.setattr(tc, "_CN_TRADE_DATES", {})
        mock_ak = MagicMock()
        mock_ak.tool_trade_date_hist_sina.return_value = pd.DataFrame({"trade_date": ["2026-09-18"]})
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            assert tc.cn_trade_dates(2026) == {"2026-09-18"}
        assert seen == [25, None]

    def test_timeout_restored_on_failure(self, monkeypatch, record_socket_timeout):
        seen = record_socket_timeout()
        monkeypatch.setattr(tc, "_CN_TRADE_DATES", {})
        mock_ak = MagicMock()
        mock_ak.tool_trade_date_hist_sina.side_effect = ConnectionError("boom")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            assert tc.cn_trade_dates(2026) == set()
        assert seen == [25, None]


class TestUsHolidays:
    """Rule-based US holiday set — the old hardcoded 2025 dates were silently wrong
    for every other year (and missed Good Friday entirely)."""

    def test_good_friday_from_easter_algorithm(self):
        # Easter Sundays: 2024-03-31, 2025-04-20, 2026-04-05
        assert tc._easter_sunday(2024).strftime("%Y-%m-%d") == "2024-03-31"
        assert tc._easter_sunday(2025).strftime("%Y-%m-%d") == "2025-04-20"
        assert tc._easter_sunday(2026).strftime("%Y-%m-%d") == "2026-04-05"

    def test_2025_dates(self):
        holidays = tc._us_holidays(2025)
        assert (1, 1) in holidays  # New Year
        assert (1, 20) in holidays  # MLK
        assert (2, 17) in holidays  # Presidents Day
        assert (4, 18) in holidays  # Good Friday
        assert (5, 26) in holidays  # Memorial Day
        assert (6, 19) in holidays  # Juneteenth (Thursday, unshifted)
        assert (7, 4) in holidays  # Independence Day (Friday, unshifted)
        assert (9, 1) in holidays  # Labor Day
        assert (11, 27) in holidays  # Thanksgiving
        assert (12, 25) in holidays  # Christmas

    def test_2024_dates(self):
        holidays = tc._us_holidays(2024)
        assert (1, 15) in holidays  # MLK
        assert (2, 19) in holidays  # Presidents Day
        assert (3, 29) in holidays  # Good Friday
        assert (5, 27) in holidays  # Memorial Day
        assert (9, 2) in holidays  # Labor Day
        assert (11, 28) in holidays  # Thanksgiving

    def test_2026_dates(self):
        holidays = tc._us_holidays(2026)
        assert (1, 19) in holidays  # MLK
        assert (2, 16) in holidays  # Presidents Day
        assert (4, 3) in holidays  # Good Friday
        assert (5, 25) in holidays  # Memorial Day
        assert (9, 7) in holidays  # Labor Day
        assert (11, 26) in holidays  # Thanksgiving
        assert (7, 4) not in holidays  # Jul 4 2026 is a Saturday
        assert (7, 3) in holidays  # observed Friday before

    def test_weekend_shift_rules(self):
        # 2021: Jul 4 Sunday → observed Mon 07-05; Christmas Saturday → Fri 12-24
        holidays_2021 = tc._us_holidays(2021)
        assert (7, 5) in holidays_2021
        assert (7, 4) not in holidays_2021
        assert (12, 24) in holidays_2021
        assert (12, 25) not in holidays_2021
        # 2022-01-01 Saturday → NYSE special case: NOT observed, 2021-12-31 stays open
        assert (12, 31) not in holidays_2021
        # 2022-06-19 Juneteenth Sunday → observed Mon 06-20 (NYSE closes from 2022)
        holidays_2022 = tc._us_holidays(2022)
        assert (6, 20) in holidays_2022
        assert (6, 19) not in holidays_2022

    def test_juneteenth_not_a_holiday_before_2022(self):
        assert (6, 19) not in tc._us_holidays(2020)
        # NYSE first closed for Juneteenth in 2022; 2021-06-18 (observed federal
        # date) was a full trading day.
        assert (6, 18) not in tc._us_holidays(2021)
        assert (6, 19) not in tc._us_holidays(2021)

    def test_new_year_sunday_observed_monday(self):
        # 2023-01-01 Sunday → observed Monday 2023-01-02
        assert (1, 2) in tc._us_holidays(2023)
        assert (1, 1) not in tc._us_holidays(2023)


@pytest.fixture
def us_fallback(monkeypatch):
    """Force the rule-based path: exchange-calendars is the primary US source, so
    simulate its absence (ImportError) for every is_trading_day call."""

    def _raise(*a, **kw):
        raise ImportError("no xcals")

    monkeypatch.setattr(tc, "_is_exchange_trading_day", _raise)


class TestUsTradingDayFallback:
    def test_holiday_not_trading(self, us_fallback):
        r = is_trading_day("US", "2026-11-26")  # Thanksgiving 2026 (Thursday)
        assert r["is_trading_day"] is False
        assert r["reason"] == "US_holiday"

    def test_good_friday_not_trading(self, us_fallback):
        assert is_trading_day("US", "2026-04-03")["is_trading_day"] is False
        assert is_trading_day("US", "2024-03-29")["is_trading_day"] is False

    def test_observed_shift_not_trading(self, us_fallback):
        # Jul 4 2026 is a Saturday; the observed closure is Friday 07-03
        assert is_trading_day("US", "2026-07-03")["is_trading_day"] is False
        assert is_trading_day("US", "2026-07-03")["reason"] == "US_holiday"

    def test_adjacent_weekday_is_trading(self, us_fallback):
        assert is_trading_day("US", "2026-07-06")["is_trading_day"] is True  # Monday after
        assert is_trading_day("US", "2026-11-27")["is_trading_day"] is True  # Friday after Thanksgiving

    def test_new_year_saturday_keeps_dec31_open(self, us_fallback):
        # 2022-01-01 was a Saturday → NYSE special case: no observed closure,
        # 2021-12-31 was a full trading day.
        assert is_trading_day("US", "2021-12-31")["is_trading_day"] is True

    def test_new_year_sunday_observed_monday_closed(self, us_fallback):
        assert is_trading_day("US", "2023-01-02")["is_trading_day"] is False
        assert is_trading_day("US", "2023-01-02")["reason"] == "US_holiday"

    def test_other_years_no_longer_silent(self, us_fallback):
        # the hardcoded set answered these wrong: 2024 MLK and 2026 Memorial Day
        assert is_trading_day("US", "2024-01-15")["is_trading_day"] is False
        assert is_trading_day("US", "2026-05-25")["is_trading_day"] is False
