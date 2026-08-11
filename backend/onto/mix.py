"""Category mix (spec 4): optional target ratios across categories, shown
against the actual shape of the period while it's being planned.

Strictly a planning aid — nothing in here feeds scoring (spec 4.8), and the
one thing outside planning that reads it is event discovery, which gives
under-filled categories suggestion priority via gaps() (spec 4.7).
"""
from __future__ import annotations

from . import scoring
from .db import execute, get_db, query


def targets(user_id: int, period_kind: str) -> dict[int, int]:
    return {
        r["category_id"]: r["percent"]
        for r in query(
            "SELECT category_id, percent FROM mix_targets WHERE user_id = ? AND period_kind = ?",
            (user_id, period_kind),
        )
    }


def set_targets(user_id: int, period_kind: str, percents: dict[int, int]) -> None:
    """Replace the user's targets for one period kind. Zero/blank drops the
    row. No sum-to-100 requirement (spec 4.4)."""
    db = get_db()
    db.execute(
        "DELETE FROM mix_targets WHERE user_id = ? AND period_kind = ?",
        (user_id, period_kind),
    )
    for cat_id, pct in percents.items():
        pct = max(0, min(int(pct), 100))
        if pct:
            db.execute(
                "INSERT INTO mix_targets (user_id, period_kind, category_id, percent)"
                " VALUES (?, ?, ?, ?)",
                (user_id, period_kind, cat_id, pct),
            )
    db.commit()


def _shares(pairs: list[tuple[int, float]]) -> dict[int, float]:
    total = sum(w for _, w in pairs)
    if not total:
        return {}
    out: dict[int, float] = {}
    for cat_id, w in pairs:
        out[cat_id] = out.get(cat_id, 0.0) + w
    return {cat_id: round(100 * w / total, 1) for cat_id, w in out.items() if w}


def planned_mix(user_id: int, period_kind: str, period_key: str) -> dict[int, float]:
    """Share of the period's commitments per category, by count — what the
    plan looks like, visible while it's still being shaped (spec 4.5)."""
    rows = scoring.rows_for(user_id, period_kind, period_key)
    return _shares([(r["cat_id"], 1.0) for r in rows])


def completed_mix(user_id: int, period_kind: str, period_key: str) -> dict[int, float]:
    """Share of what actually happened, weighting each commitment by its
    completion fraction. The planned-vs-completed gap is the interesting
    number (spec 4.6)."""
    rows = scoring.rows_for(user_id, period_kind, period_key)
    return _shares([(r["cat_id"], scoring.fraction(r)) for r in rows])


def gaps(user_id: int, period_kind: str, period_key: str) -> list[tuple[int, float]]:
    """Under-filled categories, biggest deficit first — discovery's priority
    list (spec 4.7). Empty when the user set no mix."""
    tgt = targets(user_id, period_kind)
    if not tgt:
        return []
    planned = planned_mix(user_id, period_kind, period_key)
    out = [
        (cat_id, round(pct - planned.get(cat_id, 0.0), 1))
        for cat_id, pct in tgt.items()
        if pct - planned.get(cat_id, 0.0) > 0
    ]
    return sorted(out, key=lambda p: -p[1])


def bar_data(user_id: int, period_kind: str, period_key: str) -> list[dict]:
    """Rows for the live mix bar: every category with a target or activity."""
    tgt = targets(user_id, period_kind)
    if not tgt:
        return []
    planned = planned_mix(user_id, period_kind, period_key)
    cats = query("SELECT id, name, slug FROM categories WHERE retired = 0 ORDER BY position")
    rows = []
    for c in cats:
        target = tgt.get(c["id"], 0)
        actual = planned.get(c["id"], 0.0)
        if target or actual:
            rows.append(
                {"name": c["name"], "slug": c["slug"], "target": target, "actual": actual}
            )
    return rows
