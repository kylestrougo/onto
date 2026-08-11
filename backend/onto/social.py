"""Friendships and shared goals (spec 7).

A friendship is one row with a normalised (lo, hi) pair — both directions,
one UNIQUE constraint. "Follow" collapsed to mutual friends on purpose: the
feed and leaderboard are friend-scoped, and one concept is plenty for an
app serving a circle of friends.
"""
from __future__ import annotations

from .db import execute, query


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def friendship_between(a: int, b: int):
    lo, hi = _pair(a, b)
    return query(
        "SELECT * FROM friendships WHERE user_lo = ? AND user_hi = ?", (lo, hi), one=True
    )


def are_friends(a: int, b: int) -> bool:
    row = friendship_between(a, b)
    return bool(row and row["status"] == "accepted")


def request_friend(user_id: int, username: str) -> str | None:
    """Send (or re-affirm) a friend request. Returns an error string or None."""
    other = query("SELECT id FROM users WHERE username = ?", (username.strip(),), one=True)
    if not other:
        return "No one by that name here."
    if other["id"] == user_id:
        return "That's you."
    existing = friendship_between(user_id, other["id"])
    if existing:
        if existing["status"] == "accepted":
            return "Already friends."
        if existing["requester_id"] == user_id:
            return "Request already sent."
        # They asked first — this counts as accepting.
        execute("UPDATE friendships SET status = 'accepted' WHERE id = ?", (existing["id"],))
        return None
    lo, hi = _pair(user_id, other["id"])
    execute(
        "INSERT INTO friendships (user_lo, user_hi, requester_id) VALUES (?, ?, ?)",
        (lo, hi, user_id),
    )
    return None


def respond(user_id: int, friendship_id: int, accept: bool) -> None:
    """Accept or decline a request addressed to user_id."""
    row = query(
        "SELECT * FROM friendships WHERE id = ? AND status = 'pending'"
        " AND requester_id != ? AND (user_lo = ? OR user_hi = ?)",
        (friendship_id, user_id, user_id, user_id),
        one=True,
    )
    if not row:
        return
    if accept:
        execute("UPDATE friendships SET status = 'accepted' WHERE id = ?", (friendship_id,))
    else:
        execute("DELETE FROM friendships WHERE id = ?", (friendship_id,))


def unfriend(user_id: int, other_id: int) -> None:
    lo, hi = _pair(user_id, other_id)
    execute("DELETE FROM friendships WHERE user_lo = ? AND user_hi = ?", (lo, hi))


def friends_of(user_id: int):
    """Accepted friends, as user rows."""
    return query(
        "SELECT u.id, u.username FROM friendships f"
        " JOIN users u ON u.id = CASE WHEN f.user_lo = ? THEN f.user_hi ELSE f.user_lo END"
        " WHERE (f.user_lo = ? OR f.user_hi = ?) AND f.status = 'accepted'"
        " ORDER BY u.username COLLATE NOCASE",
        (user_id, user_id, user_id),
    )


def pending_for(user_id: int):
    """Incoming requests awaiting this user's answer."""
    return query(
        "SELECT f.id, u.username FROM friendships f"
        " JOIN users u ON u.id = f.requester_id"
        " WHERE (f.user_lo = ? OR f.user_hi = ?) AND f.status = 'pending'"
        " AND f.requester_id != ?",
        (user_id, user_id, user_id),
    )


def pending_from(user_id: int):
    """Outgoing requests this user is waiting on."""
    return query(
        "SELECT f.id, u.username FROM friendships f"
        " JOIN users u ON u.id = CASE WHEN f.user_lo = ? THEN f.user_hi ELSE f.user_lo END"
        " WHERE (f.user_lo = ? OR f.user_hi = ?) AND f.status = 'pending'"
        " AND f.requester_id = ?",
        (user_id, user_id, user_id, user_id),
    )


# ── Shared goals ─────────────────────────────────────────────────────────


def invite_to_goal(user_id: int, goal_id: int, username: str) -> str | None:
    """Invite a friend into one of your goals (spec 7.5)."""
    other = query("SELECT id FROM users WHERE username = ?", (username.strip(),), one=True)
    if not other:
        return "No one by that name here."
    if not are_friends(user_id, other["id"]):
        return "You can only share goals with friends."
    if query(
        "SELECT 1 FROM goal_members WHERE goal_id = ? AND user_id = ?",
        (goal_id, other["id"]),
        one=True,
    ):
        return "They're already in."
    existing = query(
        "SELECT * FROM goal_invites WHERE goal_id = ? AND to_id = ?",
        (goal_id, other["id"]),
        one=True,
    )
    if existing and existing["status"] == "pending":
        return "Invite already sent."
    if existing:
        execute(
            "UPDATE goal_invites SET status = 'pending', from_id = ? WHERE id = ?",
            (user_id, existing["id"]),
        )
    else:
        execute(
            "INSERT INTO goal_invites (goal_id, from_id, to_id) VALUES (?, ?, ?)",
            (goal_id, user_id, other["id"]),
        )
    return None


def invites_for(user_id: int):
    return query(
        "SELECT gi.id, g.title, u.username AS from_username FROM goal_invites gi"
        " JOIN goals g ON g.id = gi.goal_id"
        " JOIN users u ON u.id = gi.from_id"
        " WHERE gi.to_id = ? AND gi.status = 'pending'",
        (user_id,),
    )


def respond_invite(user_id: int, invite_id: int, accept: bool) -> int | None:
    """Accept/decline a goal invite. Returns the goal_id on accept."""
    row = query(
        "SELECT * FROM goal_invites WHERE id = ? AND to_id = ? AND status = 'pending'",
        (invite_id, user_id),
        one=True,
    )
    if not row:
        return None
    if accept:
        execute(
            "INSERT OR IGNORE INTO goal_members (goal_id, user_id, role) VALUES (?, ?, 'member')",
            (row["goal_id"], user_id),
        )
        execute("UPDATE goal_invites SET status = 'accepted' WHERE id = ?", (invite_id,))
        return row["goal_id"]
    execute("UPDATE goal_invites SET status = 'declined' WHERE id = ?", (invite_id,))
    return None
