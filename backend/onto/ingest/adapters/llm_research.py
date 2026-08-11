"""Tier 3: scheduled LLM web research via self-hosted SearXNG (spec 8.2).

The curio lesson, applied: retrieval is a separate layer feeding extraction.
SearXNG finds candidate pages from the taxonomy's search terms, the page is
fetched and stripped here, and the LLM's only job is to read that one page
and emit structured events. Nothing it says enters the corpus until the
hard validator passes: parseable future date, recognised borough (or none),
and — non-negotiably — a URL that answers 200 right now (spec 10.4's spirit
at ingest time). Everything else is dropped and counted, and a run that
drops more than 80% of what the model produced logs loudly: that's a
misbehaving model or a garbage page, and someone should look.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Iterable

from flask import current_app

from ... import llm, prompts
from ...db import query
from .. import base, searxng
from ..base import RawEvent, strip_html
from ..verify import check_url

log = logging.getLogger(__name__)

PAGES_PER_SUBCATEGORY = 3
SUBCATEGORIES_PER_RUN = 4
KNOWN_BOROUGHS = {"", "manhattan", "brooklyn", "queens", "bronx", "staten island"}


def _thin_subcategories(limit: int) -> list:
    """The subcategories with the least upcoming corpus coverage — where
    research helps most (also how mix gaps get fillable, spec 4.7)."""
    return query(
        "SELECT s.id, s.slug, s.name, s.search_terms_json, c.slug AS cat_slug"
        " FROM subcategories s JOIN categories c ON c.id = s.category_id"
        " WHERE s.retired = 0 AND c.retired = 0 AND s.search_terms_json != '[]'"
        " ORDER BY ("
        "   SELECT COUNT(*) FROM events e WHERE e.subcategory_id = s.id"
        "   AND e.status = 'active' AND e.starts_at BETWEEN datetime('now')"
        "   AND datetime('now', '+14 days')"
        " ) ASC, RANDOM() LIMIT ?",
        (limit,),
    )


def _page_text(url: str) -> str:
    resp = base.http_get(url)
    text = strip_html(re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", resp.text))
    return re.sub(r"\s+", " ", text)


def _valid(ev: dict, page_url: str) -> str | None:
    """Why an extracted event is unusable, or None when it's good."""
    title = (ev.get("title") or "").strip()
    if not title:
        return "no title"
    raw_date = (ev.get("date") or "").strip()
    try:
        when = date.fromisoformat(raw_date)
    except ValueError:
        return f"bad date {raw_date!r}"
    if when < date.today():
        return "past date"
    if (ev.get("borough") or "").strip().lower() not in KNOWN_BOROUGHS:
        return f"unknown borough {ev.get('borough')!r}"
    url = (ev.get("url") or "").strip()
    if not url.startswith("http"):
        return "no url"
    # The model may only cite the page it read or a link that page contains;
    # any other domain is treated as invented.
    if url != page_url and not check_url(url):
        return "url did not answer 200"
    return None


def _cost_cents(raw: str) -> tuple[int | None, bool]:
    raw = (raw or "").strip().lower()
    if raw == "free":
        return 0, True
    m = re.match(r"\$?(\d+(?:\.\d{1,2})?)", raw)
    if m:
        cents = int(round(float(m.group(1)) * 100))
        return cents, cents == 0
    return None, False


class LlmResearchAdapter:
    def fetch(self, config: dict) -> Iterable[RawEvent]:
        if not current_app.config["SEARXNG_URL"]:
            log.info("research: SEARXNG_URL not set; skipping tier-3 research")
            return
        city = current_app.config["CITY"]
        month_hint = datetime.now().strftime("%B %Y")
        produced = dropped = 0

        for sub in _thin_subcategories(config.get("subcategories_per_run", SUBCATEGORIES_PER_RUN)):
            terms = json.loads(sub["search_terms_json"])
            if not terms:
                continue
            q = f"{terms[0]} {city} {month_hint}"
            for result in searxng.search(q, limit=config.get("pages", PAGES_PER_SUBCATEGORY)):
                try:
                    text = _page_text(result["url"])
                except Exception as exc:  # a dead page is normal, not fatal
                    log.info("research: fetch failed %s: %s", result["url"], exc)
                    continue
                if len(text) < 200:
                    continue
                system, user = prompts.research_extract(result["url"], text, city)
                try:
                    parsed = llm.generate(system, user, intent="research_extract")
                except llm.LLMError as exc:
                    log.warning("research: extraction failed for %s: %s", result["url"], exc)
                    continue
                for ev in parsed.get("events", []) or []:
                    if not isinstance(ev, dict):
                        continue
                    produced += 1
                    problem = _valid(ev, result["url"])
                    if problem:
                        dropped += 1
                        log.info("research: dropped %r: %s", ev.get("title"), problem)
                        continue
                    when = ev["date"].strip()
                    at = (ev.get("time") or "").strip() or "00:00"
                    cents, free = _cost_cents(ev.get("cost", ""))
                    yield RawEvent(
                        external_id=f"{base.slugify(ev['title'])}:{when}",
                        title=ev["title"].strip(),
                        url=ev["url"].strip(),
                        starts_at=f"{when} {at}:00" if len(at) == 5 else f"{when} 00:00:00",
                        venue_name=(ev.get("venue") or "").strip(),
                        borough=(ev.get("borough") or "").strip(),
                        description=(ev.get("description") or "").strip(),
                        cost_cents=cents,
                        is_free=free,
                        category_hint=f"{sub['cat_slug']}/{sub['slug']}",
                        raw={"page": result["url"], "query": q},
                    )

        if produced and dropped / produced > 0.8:
            log.error(
                "research: dropped %d of %d extracted events — model or pages misbehaving",
                dropped, produced,
            )
