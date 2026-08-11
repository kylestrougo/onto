"""Week/month period keys and their date math — the one module allowed to
think about timezones.

A period key is a string: '2026-W33' (ISO week, Monday start) or '2026-08'.
Keys are computed in the *user's* timezone, then treated as opaque,
chronologically-sortable strings everywhere else. Every rollover, score, and
digest decision hangs off this module, so it carries the densest tests.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def safe_zone(tz_name: str) -> ZoneInfo:
    """The user's zone, or the configured default for '' / garbage.

    Timezone strings come from browsers and old rows; a bad one must never
    take down a page or a cron job.
    """
    for candidate in (tz_name, current_app.config["DEFAULT_TZ"]):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def today_for(tz_name: str) -> date:
    """Today on the user's own clock — 11pm Sunday in New York is still this
    week there, even though UTC has moved on."""
    return datetime.now(safe_zone(tz_name)).date()


def week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def key_for(kind: str, d: date) -> str:
    return week_key(d) if kind == "week" else month_key(d)


def current_key(kind: str, tz_name: str) -> str:
    return key_for(kind, today_for(tz_name))


def valid_key(kind: str, key: str) -> bool:
    if kind == "week":
        m = WEEK_RE.match(key)
        if not m:
            return False
        week = int(m.group(2))
        if not 1 <= week <= 53:
            return False
        try:
            date.fromisocalendar(int(m.group(1)), week, 1)
        except ValueError:  # week 53 in a 52-week year
            return False
        return True
    if kind == "month":
        m = MONTH_RE.match(key)
        return bool(m) and 1 <= int(m.group(2)) <= 12
    return False


def bounds(kind: str, key: str) -> tuple[date, date]:
    """Inclusive first and last date of the period."""
    if kind == "week":
        m = WEEK_RE.match(key)
        start = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
        return start, start + timedelta(days=6)
    m = MONTH_RE.match(key)
    year, month = int(m.group(1)), int(m.group(2))
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def shift(kind: str, key: str, delta: int) -> str:
    """The key `delta` periods away (delta may be negative)."""
    start, _ = bounds(kind, key)
    if kind == "week":
        return week_key(start + timedelta(weeks=delta))
    year, month = start.year, start.month + delta
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year}-{month:02d}"


def label(kind: str, key: str) -> str:
    """Human name: 'Aug 11 – Aug 17' / 'August 2026'."""
    start, end = bounds(kind, key)
    if kind == "month":
        return f"{_MONTHS[start.month - 1]} {start.year}"
    return (
        f"{_MONTHS[start.month - 1][:3]} {start.day} – "
        f"{_MONTHS[end.month - 1][:3]} {end.day}"
    )


def month_of_week(key: str) -> str:
    """The month key a week belongs to (by its Monday), for the month view."""
    start, _ = bounds("week", key)
    return month_key(start)
