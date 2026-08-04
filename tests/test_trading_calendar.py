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
