"""The activity feed and leaderboard (spec 7.3, 7.7).

Rows are written at action time; visibility is enforced at read time by
joining the goal and applying its current visibility — so making a goal
private later also erases its history from friends' feeds. At friend-circle
scale there is no fan-out problem to engineer around.
"""
from __future__ import annotations

import json

from . import scoring, social
from .db import execute, query

def record(user_id: int, verb: str, goal_id: int | None = None,
           commitment_id: int | None = None, event_id: int | None = None,
           meta: dict | None = None) -> None:
    execute(
        "INSERT INTO activity (user_id, verb, goal_id, commitment_id, event_id, meta_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, verb, goal_id, commitment_id, event_id, json.dumps(meta or {})),
    )


def feed_for(viewer_id: int, before_id: int | None = None, limit: int = 50):
    """Friends' recent activity the viewer may see, newest first. Keyset
    pagination via before_id. A goal is visible when the viewer is in it,
    it's public, or it's friends-only and they're accepted friends."""
    friend_ids = [f["id"] for f in social.friends_of(viewer_id)]
    if not friend_ids:
        return []
    before = before_id or 2**62
    marks = ",".join("?" * len(friend_ids))
    sql = (
        "SELECT a.*, u.username, g.title AS goal_title, g.kind AS goal_kind,"
        " c.name AS category_name, c.slug AS category_slug"
        " FROM activity a"
        " JOIN users u ON u.id = a.user_id"
        " JOIN goals g ON g.id = a.goal_id"
        " JOIN categories c ON c.id = g.category_id"
        f" WHERE a.user_id IN ({marks}) AND a.id < ? AND"
        " (EXISTS (SELECT 1 FROM goal_members vm WHERE vm.goal_id = g.id AND vm.user_id = ?)"
        "  OR g.visibility = 'public'"
        "  OR (g.visibility = 'friends' AND EXISTS ("
        "        SELECT 1 FROM friendships f WHERE f.status = 'accepted'"
        "        AND f.user_lo = MIN(a.user_id, ?) AND f.user_hi = MAX(a.user_id, ?))))"
        " ORDER BY a.id DESC LIMIT ?"
    )
    return query(sql, (*friend_ids, before, viewer_id, viewer_id, viewer_id, limit))


def visible_goals(viewer_id: int, owner_id: int):
    """The owner's goals this viewer may see (for profiles and the spec 5.5
    score-visibility rule)."""
    return query(
        "SELECT g.*, c.name AS category_name, c.slug AS category_slug"
        " FROM goals g JOIN categories c ON c.id = g.category_id"
        " JOIN goal_members om ON om.goal_id = g.id AND om.user_id = ?"
        " WHERE g.retired_at IS NULL AND"
        " (EXISTS (SELECT 1 FROM goal_members vm WHERE vm.goal_id = g.id AND vm.user_id = ?)"
        "  OR g.visibility = 'public'"
        "  OR (g.visibility = 'friends' AND EXISTS ("
        "        SELECT 1 FROM friendships f WHERE f.status = 'accepted'"
        "        AND f.user_lo = MIN(?, ?) AND f.user_hi = MAX(?, ?))))"
        " ORDER BY c.position, g.title COLLATE NOCASE",
        (owner_id, viewer_id, owner_id, viewer_id, owner_id, viewer_id),
    )


def leaderboard(viewer_id: int, week_key: str) -> list[dict]:
    """This week's scores for the viewer and their friends (spec 7.7).

    A friend's score shows only when at least one of their goals is visible
    to the viewer (spec 5.5) — otherwise the row reads as private.
    """
    people = [{"id": viewer_id, "username": "you"}] + [
        dict(f) for f in social.friends_of(viewer_id)
    ]
    rows = []
    for p in people:
        can_see = p["id"] == viewer_id or bool(visible_goals(viewer_id, p["id"]))
        score = scoring.period_score(p["id"], "week", week_key) if can_see else None
        rows.append(
            {
                "user_id": p["id"],
                "username": p["username"],
                "visible": can_see,
                "earned": score["earned"] if score else None,
                "percent": score["percent"] if score else None,
            }
        )
    rows.sort(key=lambda r: (r["earned"] is None, -(r["earned"] or 0)))
    return rows
