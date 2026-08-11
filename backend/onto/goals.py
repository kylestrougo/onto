"""The goal library. Goals live here forever; commitments reference them.

Ownership is membership: every goal has rows in goal_members, and every read
joins through it — no separate ACL layer.
"""
from __future__ import annotations

from .db import execute, query

KINDS = ("countable", "binary", "deadline", "window", "open", "novelty")
MAX_TITLE = 120
MAX_NOTES = 500

# Plain-English, one-line explanations surfaced next to the kind picker
# (spec 11.3). Keys are the DB values; the UI never says "countable".
KIND_HELP = {
    "countable": ("A number of times", "e.g. Work out 3 times this week"),
    "binary": ("Once is done", "e.g. See a friend this week"),
    "deadline": ("Done by a date", "e.g. Finish the course by the 20th"),
    "window": ("Sometime this month", "e.g. Visit a museum this month"),
    "open": ("Open-ended", 'e.g. "Run more" — you pick a number each week'),
    "novelty": ("Something new", "e.g. Try something new — we'll find you options"),
}


def is_member(user_id: int, goal_id: int) -> bool:
    return bool(
        query(
            "SELECT 1 FROM goal_members WHERE goal_id = ? AND user_id = ?",
            (goal_id, user_id),
            one=True,
        )
    )


def own_goal(user_id: int, goal_id: int):
    """The goal row, only if this user is a member of it."""
    return query(
        "SELECT g.* FROM goals g JOIN goal_members m ON m.goal_id = g.id"
        " WHERE g.id = ? AND m.user_id = ?",
        (goal_id, user_id),
        one=True,
    )


def create(
    user_id: int,
    title: str,
    kind: str,
    category_id: int,
    subcategory_id: int | None,
    default_target: int | None,
    recurring: bool,
    notes: str = "",
) -> tuple[int | None, str | None]:
    """Create a goal owned by user_id. Returns (goal_id, error)."""
    title = (title or "").strip()[:MAX_TITLE]
    notes = (notes or "").strip()[:MAX_NOTES]
    if not title:
        return None, "Give it a name."
    if kind not in KINDS:
        return None, "Pick what kind of thing this is."
    if kind == "countable" and not (default_target and default_target >= 1):
        return None, "How many times? Pick a number."
    cat = query(
        "SELECT id FROM categories WHERE id = ? AND retired = 0", (category_id,), one=True
    )
    if not cat:
        return None, "Pick a category."
    if subcategory_id:
        sub = query(
            "SELECT id FROM subcategories WHERE id = ? AND category_id = ? AND retired = 0",
            (subcategory_id, category_id),
            one=True,
        )
        if not sub:
            return None, "That subcategory doesn't match the category."
    goal_id = execute(
        "INSERT INTO goals (created_by, title, kind, category_id, subcategory_id,"
        " default_target, recurring, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            title,
            kind,
            category_id,
            subcategory_id or None,
            default_target if kind in ("countable",) else None,
            1 if recurring else 0,
            notes,
        ),
    )
    execute(
        "INSERT INTO goal_members (goal_id, user_id, role) VALUES (?, ?, 'owner')",
        (goal_id, user_id),
    )
    return goal_id, None


def update(
    user_id: int,
    goal_id: int,
    title: str,
    category_id: int,
    subcategory_id: int | None,
    default_target: int | None,
    recurring: bool,
    notes: str = "",
) -> str | None:
    """Edit mutable fields. Kind is fixed after creation — history depends on
    it. Returns an error message or None."""
    goal = own_goal(user_id, goal_id)
    if not goal:
        return "Not your goal."
    title = (title or "").strip()[:MAX_TITLE]
    if not title:
        return "Give it a name."
    if goal["kind"] == "countable" and not (default_target and default_target >= 1):
        return "How many times? Pick a number."
    execute(
        "UPDATE goals SET title = ?, category_id = ?, subcategory_id = ?,"
        " default_target = ?, recurring = ?, notes = ? WHERE id = ?",
        (
            title,
            category_id,
            subcategory_id or None,
            default_target if goal["kind"] == "countable" else goal["default_target"],
            1 if recurring else 0,
            (notes or "").strip()[:MAX_NOTES],
            goal_id,
        ),
    )
    return None


def retire(user_id: int, goal_id: int) -> None:
    execute(
        "UPDATE goals SET retired_at = datetime('now') WHERE id = ? AND retired_at IS NULL"
        " AND id IN (SELECT goal_id FROM goal_members WHERE user_id = ?)",
        (goal_id, user_id),
    )


def unretire(user_id: int, goal_id: int) -> None:
    execute(
        "UPDATE goals SET retired_at = NULL WHERE id = ?"
        " AND id IN (SELECT goal_id FROM goal_members WHERE user_id = ?)",
        (goal_id, user_id),
    )


def library(user_id: int, include_retired: bool = True):
    """All of a user's goals with their lifetime history (spec 1.6):
    how often committed, how often fully completed."""
    return query(
        "SELECT g.*, c.name AS category_name, c.slug AS category_slug,"
        " s.name AS subcategory_name,"
        " (SELECT COUNT(*) FROM commitments cm WHERE cm.goal_id = g.id) AS times_committed,"
        " (SELECT COUNT(*) FROM commitments cm WHERE cm.goal_id = g.id"
        "   AND cm.completed_at IS NOT NULL) AS times_completed,"
        " (SELECT GROUP_CONCAT(u.username) FROM goal_members gm"
        "   JOIN users u ON u.id = gm.user_id"
        "   WHERE gm.goal_id = g.id AND gm.user_id != ?) AS shared_with"
        " FROM goals g"
        " JOIN goal_members m ON m.goal_id = g.id AND m.user_id = ?"
        " JOIN categories c ON c.id = g.category_id"
        " LEFT JOIN subcategories s ON s.id = g.subcategory_id"
        + ("" if include_retired else " WHERE g.retired_at IS NULL")
        + " ORDER BY c.position, g.retired_at IS NOT NULL, g.title COLLATE NOCASE",
        (user_id, user_id),
    )


def categories_with_subs():
    """Active taxonomy for pickers, as (category_row, [subcategory_rows])."""
    cats = query("SELECT * FROM categories WHERE retired = 0 ORDER BY position")
    subs = query("SELECT * FROM subcategories WHERE retired = 0 ORDER BY name")
    by_cat: dict[int, list] = {}
    for s in subs:
        by_cat.setdefault(s["category_id"], []).append(s)
    return [(c, by_cat.get(c["id"], [])) for c in cats]
