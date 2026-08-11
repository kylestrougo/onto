"""Digest composition (spec 9): a deterministic fact sheet from the DB, an
LLM invited to add warmth — and only warmth — and a template fallback so
the digest always sends even when every model is having a day.

Order matters and is the integrity design (spec 10): events are re-verified
BEFORE the LLM ever sees them, the LLM refers to things only by id, and the
renderer prints every date, venue, and price from the database row — never
from prose.
"""
from __future__ import annotations

import logging

from .. import commitments, llm, mix, periods, scoring
from ..db import query
from ..discovery import suggestions as sugg
from ..ingest.verify import check_url

log = logging.getLogger(__name__)

MAX_EVENTS = 3


def fact_sheet(user_row) -> dict:
    """Everything the digest may talk about, straight from the DB (spec 9.2)."""
    uid = user_row["id"]
    tz = user_row["timezone"]
    week = periods.current_key("week", tz)
    score = scoring.period_score(uid, "week", week)
    gaps = mix.gaps(uid, "week", week)
    gap_names = [
        query("SELECT name FROM categories WHERE id = ?", (cat_id,), one=True)["name"]
        for cat_id, _ in gaps[:2]
    ]

    friends_recent = query(
        "SELECT u.username, g.title FROM activity a"
        " JOIN users u ON u.id = a.user_id"
        " JOIN goals g ON g.id = a.goal_id"
        " WHERE a.verb = 'completed' AND a.created_at > datetime('now', '-7 days')"
        " AND a.user_id IN (SELECT CASE WHEN f.user_lo = :me THEN f.user_hi ELSE f.user_lo END"
        "                   FROM friendships f WHERE (f.user_lo = :me OR f.user_hi = :me)"
        "                   AND f.status = 'accepted')"
        " AND (g.visibility = 'public' OR g.visibility = 'friends'"
        "      OR EXISTS (SELECT 1 FROM goal_members vm WHERE vm.goal_id = g.id"
        "                 AND vm.user_id = :me))"
        " ORDER BY a.id DESC LIMIT 3",
        {"me": uid},
    )

    return {
        "week_label": periods.label("week", week),
        "commitments": [dict(c) for c in commitments.for_period(uid, "week", week)],
        "score": {"earned": score["earned"], "possible": score["possible"],
                  "percent": score["percent"]},
        "gap_names": gap_names,
        "friends": [dict(f) for f in friends_recent],
        "suggestions": [dict(s) for s in sugg.pending_for(uid, limit=MAX_EVENTS + 2)],
    }


def verify_candidates(facts: dict) -> dict:
    """Spec 10.4, enforced before composition: an event whose URL doesn't
    answer 200 right now, or whose moment has passed, never reaches the LLM
    — so no layer downstream can accidentally mention it."""
    kept = []
    for s in facts["suggestions"]:
        if s["starts_at"] <= query("SELECT datetime('now') AS n", one=True)["n"]:
            continue
        if not check_url(s["event_url"]):
            log.info("digest: dropped event %s (url not answering)", s["event_id"])
            continue
        kept.append(s)
        if len(kept) == MAX_EVENTS:
            break
    facts["suggestions"] = kept
    return facts


def _digest_prompt(facts: dict) -> tuple[str, str]:
    import json as _json

    system = (
        "You write a short, warm weekly note for one person's planner. "
        "You will get a fact sheet. Respond with a single bare JSON object: "
        '{"opening": "...", "goal_notes": [{"commitment_id": N, "text": "..."}], '
        '"event_picks": [{"event_id": N, "why": "..."}], "closing": "..."}. '
        "Hard rules: refer to events and commitments ONLY by their ids from "
        "the fact sheet — never invent one. Never write dates, times, "
        "weekdays, addresses, venue names, prices, or URLs in any text field; "
        "the app prints those itself from its records. Keep every text field "
        "under 200 characters. Encouraging, specific, no exclamation-mark "
        "pile-ups, no corporate cheer."
    )
    user = "Fact sheet:\n" + _json.dumps(facts, default=str)[:5000]
    return system, user


def llm_compose(facts: dict) -> dict | None:
    """One composition attempt + one retry, both validated. None → caller
    falls back to the template (the digest itself never fails)."""
    from . import validate

    allowed_commitments = {c["id"] for c in facts["commitments"]}
    allowed_events = {s["event_id"] for s in facts["suggestions"]}
    system, user = _digest_prompt(facts)
    for _ in range(2):
        try:
            parsed = llm.generate(system, user, intent="digest")
        except llm.LLMError as exc:
            log.warning("digest: generation failed: %s", exc)
            continue
        clean = validate.clean(parsed, allowed_commitments, allowed_events)
        if clean is not None:
            return clean
    return None


def fallback_prose(facts: dict) -> dict:
    """Deterministic stand-in with the same shape as the LLM's output."""
    done = sum(1 for c in facts["commitments"] if c["completed_at"])
    total = len(facts["commitments"])
    if total and done == total:
        opening = "Clean sweep last check — everything you planned, done."
    elif total:
        opening = f"You're {done} of {total} through what you set out to do."
    else:
        opening = "A fresh week, nothing planned yet — pick one thing you'd be glad to have done."
    return {
        "opening": opening,
        "goal_notes": [],
        "event_picks": [{"event_id": s["event_id"], "why": s["reason"]} for s in facts["suggestions"]],
        "closing": "",
    }


def compose(user_row) -> tuple[dict, dict]:
    """(facts, prose) — verified facts plus safe prose, LLM or fallback."""
    facts = verify_candidates(fact_sheet(user_row))
    prose = llm_compose(facts) or fallback_prose(facts)
    return facts, prose
