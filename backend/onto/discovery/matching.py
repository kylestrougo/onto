"""Matching events to people (spec 8.5): a user's discovery-enabled goals ×
the corpus, filtered by location and time, boosted by mix gaps, capped so
the app is never noisy.

Deterministic scoring, no LLM anywhere near it: suggestions must be
explainable ("matches 'See some live music'"), and a nightly cron over all
users has to be cheap.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from .. import mix, periods
from ..db import execute, query

log = logging.getLogger(__name__)

MAX_PER_USER_PER_WEEK = 5
LOOKAHEAD_DAYS = 14


def discovery_goals(user_id: int):
    """Active goals with the discovery toggle on (spec 11.6: opt-in only)."""
    return query(
        "SELECT g.*, (SELECT COUNT(*) FROM goal_members gm WHERE gm.goal_id = g.id)"
        " AS member_count"
        " FROM goals g JOIN goal_members m ON m.goal_id = g.id AND m.user_id = ?"
        " WHERE g.retired_at IS NULL AND g.discovery_enabled = 1",
        (user_id,),
    )


def _candidate_events(boroughs: list[str]):
    """Active, link-verified (or not-yet-failed), future-within-lookahead,
    categorised events — optionally borough-filtered."""
    now = datetime.now(timezone.utc)
    sql = (
        "SELECT e.* FROM events e"
        " WHERE e.status = 'active' AND e.category_id IS NOT NULL"
        " AND COALESCE(e.verify_ok, 1) = 1"
        " AND e.starts_at BETWEEN ? AND ?"
    )
    args: list = [
        now.strftime("%Y-%m-%d %H:%M:%S"),
        (now + timedelta(days=LOOKAHEAD_DAYS)).strftime("%Y-%m-%d %H:%M:%S"),
    ]
    if boroughs:
        marks = ",".join("?" * len(boroughs))
        # Events with no borough stay eligible — plenty of good sources
        # can't say, and dropping them would starve discovery.
        sql += f" AND (e.borough = '' OR e.borough IN ({marks}))"
        args.extend(boroughs)
    return query(sql, tuple(args))


def score_event(event, goals, gap_category_ids: set[int]) -> tuple[float, dict | None]:
    """(score, best_matching_goal). Zero score means no match at all."""
    best_goal, goal_points = None, 0.0
    for goal in goals:
        if event["subcategory_id"] and goal["subcategory_id"] == event["subcategory_id"]:
            pts = 3.0
        elif goal["category_id"] == event["category_id"]:
            pts = 1.0
        else:
            continue
        if pts > goal_points:
            best_goal, goal_points = goal, pts
    if best_goal is None:
        return 0.0, None

    score = goal_points
    if event["category_id"] in gap_category_ids:
        score += 2.0  # under-filled categories get priority (spec 4.7)
    score += (4 - event["tier"]) * 0.5  # better provenance ranks higher
    if event["is_free"]:
        score += 1.0
    days_out = (
        datetime.fromisoformat(event["starts_at"]) - datetime.now()
    ).days
    if days_out <= 7:
        score += 0.5  # this week beats next week
    return score, best_goal


def _suggested_this_week(user_id: int) -> int:
    return query(
        "SELECT COUNT(*) AS n FROM suggestions WHERE user_id = ?"
        " AND created_at > datetime('now', '-7 days')",
        (user_id,),
        one=True,
    )["n"]


def _already_suggested(user_id: int) -> set[int]:
    """Any prior suggestion in any status — dismissed never resurfaces."""
    return {
        r["event_id"]
        for r in query("SELECT event_id FROM suggestions WHERE user_id = ?", (user_id,))
    }


def run_for_user(user) -> int:
    """Insert up to the weekly cap of fresh suggestions for one user."""
    goals = discovery_goals(user["id"])
    if not goals:
        return 0
    budget = MAX_PER_USER_PER_WEEK - _suggested_this_week(user["id"])
    if budget <= 0:
        return 0

    boroughs = [b for b in json.loads(user["boroughs_json"] or "[]") if b]
    week_key = periods.current_key("week", user["timezone"])
    gaps = {cat_id for cat_id, _ in mix.gaps(user["id"], "week", week_key)}
    seen = _already_suggested(user["id"])

    scored = []
    for event in _candidate_events(boroughs):
        if event["id"] in seen:
            continue
        score, goal = score_event(event, goals, gaps)
        if score > 0:
            scored.append((score, event, goal))
    scored.sort(key=lambda t: -t[0])

    made = 0
    for score, event, goal in scored[:budget]:
        group_key = (
            f"g{goal['id']}:e{event['id']}" if goal["member_count"] > 1 else None
        )
        execute(
            "INSERT OR IGNORE INTO suggestions (user_id, event_id, goal_id, group_key,"
            " score, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (
                user["id"], event["id"], goal["id"], group_key,
                round(score, 2), f"matches “{goal['title']}”",
            ),
        )
        made += 1
    return made


def run_all() -> int:
    """Nightly cron body: every user with any discovery-enabled goal."""
    users = query(
        "SELECT DISTINCT u.* FROM users u"
        " JOIN goal_members m ON m.user_id = u.id"
        " JOIN goals g ON g.id = m.goal_id"
        " WHERE g.discovery_enabled = 1 AND g.retired_at IS NULL"
    )
    total = 0
    for user in users:
        total += run_for_user(user)
    return total
