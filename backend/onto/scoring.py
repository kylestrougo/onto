"""Scoring (spec 5). Weights are admin-global — points per category and
subcategory — so scores are comparable between users by construction. The
default config is flat (every category 1.0), so there is no starting bias.

Computed on read: a user-period is a handful of rows, and a cache would just
be one more thing to invalidate. Mix (spec 4) never enters this module.
"""
from __future__ import annotations

from .db import query


def rows_for(user_id: int, period_kind: str, period_key: str):
    """Commitments with their effective points and progress for one period."""
    return query(
        "SELECT cm.*, g.title, g.kind,"
        " c.name AS category_name, c.slug AS category_slug, c.id AS cat_id,"
        " s.name AS subcategory_name,"
        " COALESCE(s.points, c.points) AS points,"
        " (SELECT COUNT(*) FROM completions cp WHERE cp.commitment_id = cm.id) AS logged"
        " FROM commitments cm"
        " JOIN goals g ON g.id = cm.goal_id"
        " JOIN goal_members m ON m.goal_id = g.id AND m.user_id = ?"
        " JOIN categories c ON c.id = g.category_id"
        " LEFT JOIN subcategories s ON s.id = g.subcategory_id"
        " WHERE cm.period_kind = ? AND cm.period_key = ?"
        " ORDER BY cm.id",
        (user_id, period_kind, period_key),
    )


def fraction(row) -> float:
    """How much of this commitment happened, 0..1.

    Countable/open earn partial credit (spec 5.3): 2 of 3 is 67%, capped at
    100%. Deadline pays only for on-time completion — a late check-off still
    counts in the goal's history, just not in the score.
    """
    kind = row["kind"]
    if kind in ("countable", "open"):
        target = row["target"] or 1
        return min(row["logged"] / target, 1.0)
    if kind == "deadline":
        done = row["completed_at"]
        if not done or not row["due_date"]:
            return 0.0
        return 1.0 if done[:10] <= row["due_date"] else 0.0
    # binary, window, novelty
    return 1.0 if row["completed_at"] else 0.0


def period_score(user_id: int, period_kind: str, period_key: str) -> dict:
    """Earned and possible points for one period (spec 5.4)."""
    rows = rows_for(user_id, period_kind, period_key)
    earned = sum(row["points"] * fraction(row) for row in rows)
    possible = sum(row["points"] for row in rows)
    return {
        "earned": round(earned, 2),
        "possible": round(possible, 2),
        "percent": round(100 * earned / possible) if possible else 0,
        "rows": rows,
    }
