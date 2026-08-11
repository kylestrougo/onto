"""The suggestion lifecycle: pending → seen → accepted | dismissed | flagged,
with expiry when the event passes.

Accepting creates the commitment with the source link attached (spec 8.7);
for a novelty goal it supplies the "what". Flagging quarantines the event
immediately and can quarantine the whole source (spec 10.6).
"""
from __future__ import annotations

import logging

from .. import commitments, feed, periods
from ..db import execute, query

log = logging.getLogger(__name__)

# A source is quarantined when this many distinct users flag its events
# inside 30 days.
QUARANTINE_FLAGGERS = 3


def pending_for(user_id: int, limit: int = 5):
    return query(
        "SELECT s.*, e.title AS event_title, e.url AS event_url,"
        " e.starts_at, e.venue_name, e.borough, e.cost_cents, e.is_free, e.tier,"
        " src.name AS source_name, g.title AS goal_title, g.kind AS goal_kind"
        " FROM suggestions s"
        " JOIN events e ON e.id = s.event_id"
        " JOIN sources src ON src.id = e.source_id"
        " LEFT JOIN goals g ON g.id = s.goal_id"
        " WHERE s.user_id = ? AND s.status IN ('pending', 'seen')"
        " AND e.status = 'active'"
        " ORDER BY s.score DESC LIMIT ?",
        (user_id, limit),
    )


def group_companions(suggestion) -> list[str]:
    """Other members who got the same shared-goal suggestion — and whether
    they've accepted — for the "for you and Alex" line."""
    if not suggestion["group_key"]:
        return []
    return [
        f"{r['username']}{' (in!)' if r['status'] == 'accepted' else ''}"
        for r in query(
            "SELECT u.username, s.status FROM suggestions s"
            " JOIN users u ON u.id = s.user_id"
            " WHERE s.group_key = ? AND s.user_id != ?",
            (suggestion["group_key"], suggestion["user_id"]),
        )
    ]


def mark_seen(user_id: int) -> None:
    execute(
        "UPDATE suggestions SET status = 'seen' WHERE user_id = ? AND status = 'pending'",
        (user_id,),
    )


def _own_actionable(user_id: int, suggestion_id: int):
    return query(
        "SELECT s.*, e.status AS event_status, e.source_id FROM suggestions s"
        " JOIN events e ON e.id = s.event_id"
        " WHERE s.id = ? AND s.user_id = ? AND s.status IN ('pending', 'seen')",
        (suggestion_id, user_id),
        one=True,
    )


def accept(user, suggestion_id: int) -> str | None:
    """Accepting creates (or binds) this week's commitment for the matched
    goal, event link attached. Returns an error message or None."""
    s = _own_actionable(user.id, suggestion_id)
    if not s:
        return "That suggestion isn't on the table."
    if not s["goal_id"]:
        return "That suggestion lost its goal."
    goal = query("SELECT * FROM goals WHERE id = ?", (s["goal_id"],), one=True)
    week_key = periods.current_key("week", user.timezone)

    existing = query(
        "SELECT * FROM commitments WHERE goal_id = ? AND period_kind = 'week'"
        " AND period_key = ?",
        (goal["id"], week_key),
        one=True,
    )
    if existing:
        # Already planned this week — bind the event to it. This is exactly
        # how a novelty goal gets its "what" (spec: Novelty).
        execute(
            "UPDATE commitments SET event_id = ?, suggestion_id = ? WHERE id = ?",
            (s["event_id"], s["id"], existing["id"]),
        )
        cid = existing["id"]
    else:
        cid, error = commitments.add(
            user.id, goal, "week", week_key,
            target=goal["default_target"],
            event_id=s["event_id"], suggestion_id=s["id"],
        )
        if error:
            return "Couldn't put that on your week — add the goal first, then accept."
    execute(
        "UPDATE suggestions SET status = 'accepted', commitment_id = ?,"
        " decided_at = datetime('now') WHERE id = ?",
        (cid, s["id"]),
    )
    feed.record(user.id, "suggestion_accepted", goal_id=goal["id"],
                commitment_id=cid, event_id=s["event_id"])
    return None


def dismiss(user_id: int, suggestion_id: int) -> None:
    execute(
        "UPDATE suggestions SET status = 'dismissed', decided_at = datetime('now')"
        " WHERE id = ? AND user_id = ? AND status IN ('pending', 'seen')",
        (suggestion_id, user_id),
    )


def flag(user_id: int, suggestion_id: int, reason: str = "") -> None:
    """Wrong/dead/misleading suggestion (spec 10.6). The event leaves the
    suggestible pool immediately; repeat-offender sources get quarantined."""
    s = _own_actionable(user_id, suggestion_id)
    if not s:
        return
    execute(
        "UPDATE suggestions SET status = 'flagged', decided_at = datetime('now') WHERE id = ?",
        (s["id"],),
    )
    execute(
        "INSERT OR IGNORE INTO event_flags (user_id, event_id, reason) VALUES (?, ?, ?)",
        (user_id, s["event_id"], (reason or "").strip()[:300]),
    )
    execute("UPDATE events SET status = 'quarantined' WHERE id = ?", (s["event_id"],))
    execute(
        "UPDATE sources SET flag_count = flag_count + 1 WHERE id = ?", (s["source_id"],)
    )

    distinct = query(
        "SELECT COUNT(DISTINCT f.user_id) AS n FROM event_flags f"
        " JOIN events e ON e.id = f.event_id"
        " WHERE e.source_id = ? AND f.created_at > datetime('now', '-30 days')",
        (s["source_id"],),
        one=True,
    )["n"]
    if distinct >= QUARANTINE_FLAGGERS:
        execute(
            "UPDATE sources SET quarantined_at = datetime('now'), enabled = 0"
            " WHERE id = ? AND quarantined_at IS NULL",
            (s["source_id"],),
        )
        execute(
            "UPDATE events SET status = 'quarantined' WHERE source_id = ?"
            " AND status = 'active'",
            (s["source_id"],),
        )
        log.warning("source %s quarantined: %d distinct users flagged it",
                    s["source_id"], distinct)


def expire_stale() -> int:
    """Housekeeping: suggestions whose event has passed stop counting
    against anyone's attention."""
    from ..db import get_db

    db = get_db()
    cur = db.execute(
        "UPDATE suggestions SET status = 'expired' WHERE status IN ('pending', 'seen')"
        " AND event_id IN (SELECT id FROM events WHERE COALESCE(ends_at, starts_at)"
        "                  < datetime('now'))"
    )
    db.commit()
    return cur.rowcount
