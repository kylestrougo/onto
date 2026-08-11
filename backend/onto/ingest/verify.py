"""Re-verification of the corpus (spec 10.4's standing half): event URLs must
still answer and events must still be in the future to be suggestible.

Runs nightly from cron. Failures mark verify_ok = 0 rather than deleting —
a venue's site being down for a night shouldn't erase real events, it just
makes them unsuggestible until a check passes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from ..db import execute, query
from .base import FETCH_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)


def check_url(url: str) -> bool:
    """True when the source URL answers 200 (HEAD first; some servers only
    do GET)."""
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.head(url, headers=headers, timeout=FETCH_TIMEOUT, allow_redirects=True)
        if resp.status_code in (405, 501):
            resp = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT, stream=True)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def expire_past_events() -> int:
    """Events whose moment has passed leave the active pool."""
    from ..db import get_db

    db = get_db()
    cur = db.execute(
        "UPDATE events SET status = 'expired' WHERE status = 'active'"
        " AND COALESCE(ends_at, starts_at) < datetime('now', '-6 hours')"
    )
    db.commit()
    return cur.rowcount


def verify_batch(limit: int = 100) -> dict:
    """Check the least-recently-verified active future events."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = query(
        "SELECT id, url FROM events WHERE status = 'active' AND starts_at > ?"
        " ORDER BY last_verified_at IS NOT NULL, last_verified_at LIMIT ?",
        (now, limit),
    )
    ok = bad = 0
    for row in rows:
        good = check_url(row["url"])
        execute(
            "UPDATE events SET verify_ok = ?, last_verified_at = datetime('now') WHERE id = ?",
            (1 if good else 0, row["id"]),
        )
        ok += good
        bad += not good
    return {"checked": len(rows), "ok": ok, "bad": bad}
