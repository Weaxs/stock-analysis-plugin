from datetime import datetime

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
