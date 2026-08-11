"""Every prompt in the app, one file. Each function returns (system, user).

The anti-fabrication stance runs through all of them, but prompts are the
belt — the validators in ingest/adapters/llm_research.py and
notify/validate.py are the braces. Neither trusts the other to have worked.
"""
from __future__ import annotations

_JSON_RULE = (
    "Answer with a single bare JSON object. No markdown fences, no prose "
    "before or after."
)


def research_extract(page_url: str, page_text: str, city: str) -> tuple[str, str]:
    """Pull real, upcoming, time-bound events out of one fetched page."""
    system = (
        "You extract real-world events from a web page for a local events "
        "database. Only report events that are explicitly described in the "
        "page text, in " + city + ". Never invent, infer, or embellish a "
        "name, date, venue, price, or URL. If the page has no clearly dated "
        "upcoming events, return an empty list. "
        + _JSON_RULE
    )
    user = (
        "Page URL: " + page_url + "\n\n"
        "Page text (extracted):\n" + page_text[:6000] + "\n\n"
        "Return JSON exactly in this shape:\n"
        '{"events": [{"title": "...", "date": "YYYY-MM-DD", '
        '"time": "HH:MM or empty", "venue": "...", '
        '"borough": "Manhattan|Brooklyn|Queens|Bronx|Staten Island or empty", '
        '"url": "the page URL above, verbatim, or a more specific link that '
        'appears in the text", "cost": "free|$N|empty", '
        '"description": "one sentence taken from the page"}]}\n'
        "Rules: only events dated today or later; the url field must be a "
        "URL that actually appears in or is the source of this page; leave "
        "fields empty rather than guessing."
    )
    return system, user
