"""Commitments: a goal dropped into a specific period. These get scored;
goals never do.

completed_at is derived state kept in step by log/unlog/complete: set the
moment a countable reaches its target or a one-shot goal is checked off,
cleared when progress drops back below. Scoring and history both read it.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from .db import execute, get_db, query

# Which goal kinds may be dropped on which period kind. Window goals belong
# to months (spec: Window type); everything else lives in weeks, and months
# also accept deadline goals whose date falls inside the month.
WEEK_KINDS = {"countable", "binary", "deadline", "open", "novelty"}
MONTH_KINDS = {"countable", "binary", "deadline", "window", "open", "novelty"}


def needs(goal, period_kind: str) -> str | None:
    """What must be supplied at drop time for this goal, if anything:
    'target' (countable with no default, open) or 'due_date' (deadline)."""
    kind = goal["kind"]
    if kind == "open":
        return "target"  # spec: open goals MUST get a target when committed
    if kind == "countable" and not goal["default_target"]:
        return "target"
    if kind == "deadline":
        return "due_date"
    return None


def add(
    user_id: int,
    goal,
    period_kind: str,
    period_key: str,
    target: int | None = None,
    due_date: str | None = None,
    event_id: int | None = None,
    suggestion_id: int | None = None,
) -> tuple[int | None, str | None]:
    """Create the commitment. Returns (commitment_id, error). Re-adding a
    goal already in the period returns the existing row (drag can double-fire)."""
    kind = goal["kind"]
    allowed = WEEK_KINDS if period_kind == "week" else MONTH_KINDS
    if kind not in allowed:
        return None, "That one is for a month, not a week — add it from the month view."

    if kind in ("countable", "open"):
        target = target or (goal["default_target"] if kind == "countable" else None)
        if not target or target < 1:
            return None, "needs_target"
        if target > 99:
            return None, "Keep the target under 100."
    else:
        target = None

    if kind == "deadline":
        if not due_date:
            return None, "needs_due_date"
        try:
            date.fromisoformat(due_date)
        except ValueError:
            return None, "That date didn't make sense."
    else:
        due_date = None

    try:
        cid = execute(
            "INSERT INTO commitments (goal_id, period_kind, period_key, target,"
            " due_date, event_id, suggestion_id, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (goal["id"], period_kind, period_key, target, due_date, event_id, suggestion_id, user_id),
        )
        return cid, None
    except sqlite3.IntegrityError:
        row = query(
            "SELECT id FROM commitments WHERE goal_id = ? AND period_kind = ? AND period_key = ?",
            (goal["id"], period_kind, period_key),
            one=True,
        )
        return (row["id"], None) if row else (None, "Couldn't add that.")


def own_commitment(user_id: int, commitment_id: int):
    """Commitment + its goal, only if the user is a member of the goal."""
    return query(
        "SELECT cm.*, g.title, g.kind, g.category_id, g.subcategory_id, g.created_by AS goal_owner,"
        " c.name AS category_name, c.slug AS category_slug, s.name AS subcategory_name,"
        " (SELECT COUNT(*) FROM goal_members gm WHERE gm.goal_id = g.id) AS member_count,"
        " (SELECT GROUP_CONCAT(DISTINCT u.username) FROM completions cp"
        "   JOIN users u ON u.id = cp.user_id WHERE cp.commitment_id = cm.id) AS logger_names,"
        " (SELECT cp.photo_path FROM completions cp WHERE cp.commitment_id = cm.id"
        "   AND cp.photo_path IS NOT NULL ORDER BY cp.id DESC LIMIT 1) AS photo_path"
        " FROM commitments cm"
        " JOIN goals g ON g.id = cm.goal_id"
        " JOIN goal_members m ON m.goal_id = g.id AND m.user_id = ?"
        " JOIN categories c ON c.id = g.category_id"
        " LEFT JOIN subcategories s ON s.id = g.subcategory_id"
        " WHERE cm.id = ?",
        (user_id, commitment_id),
        one=True,
    )


def logged_count(commitment_id: int) -> int:
    return query(
        "SELECT COUNT(*) AS n FROM completions WHERE commitment_id = ?",
        (commitment_id,),
        one=True,
    )["n"]


def _sync_completed(commitment) -> None:
    """Recompute completed_at from progress. Target NULL means 1."""
    done = logged_count(commitment["id"]) >= (commitment["target"] or 1)
    if done and not commitment["completed_at"]:
        execute(
            "UPDATE commitments SET completed_at = datetime('now') WHERE id = ?",
            (commitment["id"],),
        )
    elif not done and commitment["completed_at"]:
        execute("UPDATE commitments SET completed_at = NULL WHERE id = ?", (commitment["id"],))


def log(user_id: int, commitment, note: str = "", photo_path: str | None = None) -> None:
    """One check-off. For countables this is +1; for one-shot kinds the
    endpoint guards against logging past done."""
    execute(
        "INSERT INTO completions (commitment_id, user_id, note, photo_path) VALUES (?, ?, ?, ?)",
        (commitment["id"], user_id, (note or "").strip()[:300], photo_path),
    )
    _sync_completed(commitment)


def unlog(user_id: int, commitment) -> None:
    """Undo the most recent check-off on this commitment."""
    db = get_db()
    db.execute(
        "DELETE FROM completions WHERE id ="
        " (SELECT id FROM completions WHERE commitment_id = ? ORDER BY id DESC LIMIT 1)",
        (commitment["id"],),
    )
    db.commit()
    _sync_completed(commitment)


def remove(commitment) -> None:
    """Take the goal back out of the period. Its completions go with it —
    an uncommitted week holds no history, but past periods keep theirs."""
    execute("DELETE FROM commitments WHERE id = ?", (commitment["id"],))


def for_period(user_id: int, period_kind: str, period_key: str):
    """The user's commitments in one period, with progress, oldest first."""
    return query(
        "SELECT cm.*, g.title, g.kind, g.recurring, g.discovery_enabled,"
        " c.name AS category_name, c.slug AS category_slug, s.name AS subcategory_name,"
        " (SELECT COUNT(*) FROM completions cp WHERE cp.commitment_id = cm.id) AS logged,"
        " (SELECT COUNT(*) FROM goal_members gm WHERE gm.goal_id = g.id) AS member_count,"
        " (SELECT GROUP_CONCAT(DISTINCT u.username) FROM completions cp"
        "   JOIN users u ON u.id = cp.user_id WHERE cp.commitment_id = cm.id) AS logger_names,"
        " (SELECT cp.photo_path FROM completions cp WHERE cp.commitment_id = cm.id"
        "   AND cp.photo_path IS NOT NULL ORDER BY cp.id DESC LIMIT 1) AS photo_path"
        " FROM commitments cm"
        " JOIN goals g ON g.id = cm.goal_id"
        " JOIN goal_members m ON m.goal_id = g.id AND m.user_id = ?"
        " JOIN categories c ON c.id = g.category_id"
        " LEFT JOIN subcategories s ON s.id = g.subcategory_id"
        " WHERE cm.period_kind = ? AND cm.period_key = ?"
        " ORDER BY cm.completed_at IS NOT NULL, cm.id",
        (user_id, period_kind, period_key),
    )


def progress(user_id: int, period_kind: str, period_key: str) -> dict:
    """Header numbers: how many commitments, how many fully done."""
    row = query(
        "SELECT COUNT(*) AS total, SUM(cm.completed_at IS NOT NULL) AS done"
        " FROM commitments cm"
        " JOIN goal_members m ON m.goal_id = cm.goal_id AND m.user_id = ?"
        " WHERE cm.period_kind = ? AND cm.period_key = ?",
        (user_id, period_kind, period_key),
        one=True,
    )
    return {"total": row["total"] or 0, "done": row["done"] or 0}


def uncommitted_goals(user_id: int, period_kind: str, period_key: str):
    """Active library goals not yet in this period — the drag source. Window
    goals only offer themselves on months."""
    kind_filter = "" if period_kind == "month" else " AND g.kind != 'window'"
    return query(
        "SELECT g.*, c.name AS category_name, c.slug AS category_slug"
        " FROM goals g"
        " JOIN goal_members m ON m.goal_id = g.id AND m.user_id = ?"
        " JOIN categories c ON c.id = g.category_id"
        " WHERE g.retired_at IS NULL" + kind_filter +
        " AND g.id NOT IN (SELECT goal_id FROM commitments WHERE period_kind = ? AND period_key = ?)"
        " ORDER BY c.position, g.title COLLATE NOCASE",
        (user_id, period_kind, period_key),
    )
