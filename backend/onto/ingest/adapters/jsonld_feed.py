"""Tier 2: venue pages that embed schema.org Event markup as JSON-LD.

Museums, theatres, and ticketing pages widely publish
<script type="application/ld+json"> blocks with @type Event (or a subtype).
The admin adds a page URL as a source; no per-venue code.

config_json shape:
  {"url": "https://venue.example/whats-on",
   "borough": "Brooklyn", "category_hint": "culture/museums-exhibits"}
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterable

from .. import base
from ..base import RawEvent, strip_html

_SCRIPT_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.S | re.I,
)

EVENT_TYPES = {
    "Event", "MusicEvent", "TheaterEvent", "ExhibitionEvent", "Festival",
    "ComedyEvent", "DanceEvent", "ScreeningEvent", "SportsEvent",
    "EducationEvent", "SocialEvent", "FoodEvent", "LiteraryEvent",
    "VisualArtsEvent", "ChildrensEvent",
}


def _iso(value) -> str:
    if not value or not isinstance(value, str):
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return ""


def _walk(node):
    """Yield every dict anywhere in a JSON-LD document (top level may be a
    list, a @graph, or nested)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_event(node: dict) -> bool:
    t = node.get("@type", "")
    types = t if isinstance(t, list) else [t]
    return any(str(x) in EVENT_TYPES for x in types)


def _location_bits(node) -> tuple[str, str]:
    loc = node.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if isinstance(loc, str):
        return loc, ""
    name = str(loc.get("name", "") or "")
    addr = loc.get("address") or {}
    if isinstance(addr, str):
        return name, addr
    parts = [str(addr.get(k, "") or "") for k in ("streetAddress", "addressLocality")]
    return name, ", ".join(p for p in parts if p)


def _price_cents(node) -> tuple[int | None, bool]:
    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return None, False
    price = offers.get("price")
    try:
        cents = int(round(float(price) * 100))
        return cents, cents == 0
    except (TypeError, ValueError):
        return None, False


class JsonLdFeedAdapter:
    def fetch(self, config: dict) -> Iterable[RawEvent]:
        html = base.http_get(config["url"]).text
        for block in _SCRIPT_RE.findall(html):
            try:
                doc = json.loads(block.strip())
            except ValueError:
                continue
            for node in _walk(doc):
                if not _is_event(node):
                    continue
                starts = _iso(node.get("startDate"))
                title = str(node.get("name", "") or "").strip()
                if not title:
                    continue
                url = str(node.get("url", "") or "") or config["url"]
                venue, address = _location_bits(node)
                cents, free = _price_cents(node)
                yield RawEvent(
                    external_id=f"{title}|{starts[:10]}",
                    title=title,
                    url=url,
                    starts_at=starts,
                    ends_at=_iso(node.get("endDate")) or None,
                    venue_name=venue,
                    address=address,
                    borough=config.get("borough", ""),
                    description=strip_html(str(node.get("description", "") or ""))[:2000],
                    cost_cents=cents,
                    is_free=free,
                    category_hint=config.get("category_hint", ""),
                    raw={"jsonld_type": node.get("@type")},
                )
