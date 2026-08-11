"""The failure these tests prevent: period math that drifts at year
boundaries, DST, or for users whose midnight isn't UTC's — which would
corrupt rollover, scoring, and digests all at once."""
from datetime import date

from onto import periods


def test_week_key_and_bounds():
    assert periods.week_key(date(2026, 8, 11)) == "2026-W33"
    start, end = periods.bounds("week", "2026-W33")
    assert start == date(2026, 8, 10) and start.weekday() == 0  # Monday
    assert end == date(2026, 8, 16)


def test_week_key_year_boundary():
    # Jan 1-3 2027 belong to ISO week 2026-W53.
    assert periods.week_key(date(2027, 1, 1)) == "2026-W53"
    assert periods.week_key(date(2027, 1, 4)) == "2027-W01"
    # Dec 29 2025 opens 2026-W01.
    assert periods.week_key(date(2025, 12, 29)) == "2026-W01"


def test_month_key_and_bounds():
    assert periods.month_key(date(2026, 8, 11)) == "2026-08"
    start, end = periods.bounds("month", "2026-02")
    assert start == date(2026, 2, 1) and end == date(2026, 2, 28)
    _, dec_end = periods.bounds("month", "2026-12")
    assert dec_end == date(2026, 12, 31)


def test_shift_weeks_and_months():
    assert periods.shift("week", "2026-W33", 1) == "2026-W34"
    assert periods.shift("week", "2026-W01", -1) == "2025-W52"
    assert periods.shift("week", "2026-W53", 1) == "2027-W01"
    assert periods.shift("month", "2026-12", 1) == "2027-01"
    assert periods.shift("month", "2026-01", -1) == "2025-12"


def test_valid_key():
    assert periods.valid_key("week", "2026-W33")
    assert periods.valid_key("week", "2026-W53")  # 2026 has 53 ISO weeks
    assert not periods.valid_key("week", "2025-W53")  # 2025 does not
    assert not periods.valid_key("week", "2026-W00")
    assert not periods.valid_key("week", "2026-08")
    assert periods.valid_key("month", "2026-08")
    assert not periods.valid_key("month", "2026-13")
    assert not periods.valid_key("month", "2026-W33")


def test_current_key_respects_timezone(app):
    """23:00 Sunday in New York is Monday 03:00 UTC — the user's week must
    not roll over until *their* midnight."""
    with app.app_context():
        from unittest.mock import patch
        from datetime import datetime, timezone

        # 2026-08-17 03:00 UTC == 2026-08-16 23:00 in New York (UTC-4, DST).
        frozen = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

        with patch("onto.periods.datetime", FrozenDatetime):
            assert periods.current_key("week", "UTC") == "2026-W34"
            assert periods.current_key("week", "America/New_York") == "2026-W33"


def test_safe_zone_survives_garbage(app):
    with app.app_context():
        assert periods.safe_zone("Not/AZone") is not None
        assert periods.safe_zone("") is not None


def test_label(app):
    assert periods.label("week", "2026-W33") == "Aug 10 – Aug 16"
    assert periods.label("month", "2026-08") == "August 2026"
    # A week spanning two months names both.
    assert periods.label("week", "2026-W36") == "Aug 31 – Sep 6"
