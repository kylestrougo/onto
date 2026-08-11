"""The gate between LLM prose and anything a user reads (spec 10.2, 10.3).

The model was told to reference things by id and keep facts out of its
text. This module assumes it ignored that. Unknown ids reject the fragment
outright; text that smells like a smuggled fact — a URL, a date, a weekday,
a time, a price — rejects that fragment too. Rejection is per-fragment:
one bad note costs that note, not the digest.

Returns None only when the whole object is unusable, which sends the caller
to the deterministic fallback. Either way the user gets a digest whose
facts were printed by the server from database rows.
"""
from __future__ import annotations

import re

MAX_TEXT = 300

_SMELLS = [
    re.compile(r"https?://|www\.", re.I),                       # URLs
    re.compile(                                                  # month names
        r"\b(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|"
        r"aug(ust)?|sep(t(ember)?)?|oct(ober)?|nov(ember)?|dec(ember)?)\b\.?\s*\d",
        re.I,
    ),
    re.compile(                                                  # weekday names
        r"\b(mon|tues|wednes|thurs|fri|satur|sun)day\b", re.I
    ),
    re.compile(r"\b\d{1,2}[/.\-]\d{1,2}([/.\-]\d{2,4})?\b"),     # 8/15, 15.08.26
    re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?[ap]m\b", re.I),  # times
    re.compile(r"\$\s?\d|\b\d+\s?(dollars|bucks)\b", re.I),      # prices
    re.compile(r"\b\d{1,2}(st|nd|rd|th)\b", re.I),               # "the 15th"
]


def _text_ok(text: str) -> bool:
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        return False
    return not any(p.search(text) for p in _SMELLS)


def clean(parsed: dict, allowed_commitments: set[int], allowed_events: set[int]) -> dict | None:
    """Validated copy of the model's output, or None when nothing survives."""
    if not isinstance(parsed, dict):
        return None
    out: dict = {"opening": "", "goal_notes": [], "event_picks": [], "closing": ""}

    opening = parsed.get("opening")
    if isinstance(opening, str) and _text_ok(opening):
        out["opening"] = opening.strip()
    closing = parsed.get("closing")
    if isinstance(closing, str) and _text_ok(closing):
        out["closing"] = closing.strip()

    for note in parsed.get("goal_notes") or []:
        if not isinstance(note, dict):
            continue
        cid = note.get("commitment_id")
        text = note.get("text", "")
        # An id outside the supplied set is an invention — hard reject (10.2).
        if cid in allowed_commitments and _text_ok(text) and text.strip():
            out["goal_notes"].append({"commitment_id": cid, "text": text.strip()})

    for pick in parsed.get("event_picks") or []:
        if not isinstance(pick, dict):
            continue
        eid = pick.get("event_id")
        why = pick.get("why", "")
        if eid not in allowed_events:
            continue
        if not _text_ok(why):
            why = ""  # keep the pick, lose the tainted blurb
        out["event_picks"].append({"event_id": eid, "why": why.strip()})

    # Usable if it says anything at all.
    if out["opening"] or out["goal_notes"] or out["event_picks"]:
        return out
    return None
