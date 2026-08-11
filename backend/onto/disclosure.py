"""Progressive disclosure (spec 11.1): a new user sees add-a-goal, drop it
into the week, check it off — and nothing else. Features reveal themselves
as the basics get used.

Unlocks are one-way and persisted in user_flags, so the UI never yanks a
feature back. Rules are evaluated server-side after the actions that could
trip them; `unlocked()` is exposed to Jinja for nav/panel gating. The
settings page offers "show everything" for the impatient (stored on users).
"""
from __future__ import annotations

from .db import execute, query

# flag -> human blurb shown the moment it unlocks.
FLAGS = {
    "mix": "New: set a balance across categories from the mix page.",
    "social": "New: add friends and share goals from the friends page.",
    "months": "New: plan a whole month, not just weeks.",
}


def unlocked(user, flag: str) -> bool:
    if user.show_everything:
        return True
    return bool(
        query(
            "SELECT 1 FROM user_flags WHERE user_id = ? AND flag = ?",
            (user.id, flag),
            one=True,
        )
    )


def unlock(user_id: int, flag: str) -> bool:
    """Record an unlock. Returns True the first time only."""
    existing = query(
        "SELECT 1 FROM user_flags WHERE user_id = ? AND flag = ?", (user_id, flag), one=True
    )
    if existing:
        return False
    execute("INSERT OR IGNORE INTO user_flags (user_id, flag) VALUES (?, ?)", (user_id, flag))
    return True


def evaluate_after_completion(user_id: int) -> list[str]:
    """Called whenever the user checks something off. Returns newly-unlocked
    flags so the caller can flash the blurb."""
    fresh: list[str] = []
    row = query(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT cm.period_key) AS periods"
        " FROM completions cp JOIN commitments cm ON cm.id = cp.commitment_id"
        " WHERE cp.user_id = ?",
        (user_id,),
        one=True,
    )
    # The basics are in use: three check-offs across at least two periods.
    if row["n"] >= 3 and row["periods"] >= 2:
        for flag in ("mix", "months"):
            if unlock(user_id, flag):
                fresh.append(flag)
    # A first taste of momentum is enough to introduce friends.
    if row["n"] >= 5:
        if unlock(user_id, "social"):
            fresh.append("social")
    return fresh


def init_app(app) -> None:
    @app.template_global("unlocked")
    def _unlocked(flag: str) -> bool:
        from flask_login import current_user

        if not current_user.is_authenticated:
            return False
        return unlocked(current_user, flag)
