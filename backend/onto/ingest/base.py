"""The ingest pipeline every source flows through (spec 8).

An adapter's only job is fetch(config) -> RawEvents; the shared pipeline
here normalises, categorises against the taxonomy's search terms, dedupes
within and across sources, and upserts. Adapters do network I/O; the
pipeline never does.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
from flask import current_app

from ..db import execute, get_db, query

log = logging.getLogger(__name__)

MAX_FETCH_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT = 20
USER_AGENT = "onto-ingest/1.0 (self-hosted weekly planner)"

BOROUGHS = {"manhattan", "brooklyn", "queens", "bronx", "staten island"}


@dataclass
class RawEvent:
    external_id: str
    title: str
    url: str
    starts_at: str                     # UTC 'YYYY-MM-DD HH:MM:SS'
    ends_at: str | None = None
    venue_name: str = ""
    address: str = ""
    borough: str = ""
    description: str = ""
    cost_cents: int | None = None
    is_free: bool = False
    lat: float | None = None
    lon: float | None = None
    # A category or subcategory slug the adapter already knows, e.g. a
    # source that is inherently "culture/live-music". Beats keyword matching.
    category_hint: str = ""
    raw: dict = field(default_factory=dict)


def http_get(url: str, **kwargs) -> requests.Response:
    """The one HTTP door for adapters: UA set, timeout enforced, body capped."""
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=FETCH_TIMEOUT,
        stream=True,
        **kwargs,
    )
    resp.raise_for_status()
    content = resp.raw.read(MAX_FETCH_BYTES + 1, decode_content=True)
    if len(content) > MAX_FETCH_BYTES:
        raise ValueError(f"response over {MAX_FETCH_BYTES} bytes: {url}")
    resp._content = content
    return resp


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").replace("&amp;", "&").strip()


def normalise_borough(raw: str) -> str:
    b = (raw or "").strip().lower()
    aliases = {
        "new york": "Manhattan", "ny": "Manhattan", "manhattan": "Manhattan",
        "brooklyn": "Brooklyn", "kings": "Brooklyn",
        "queens": "Queens",
        "bronx": "Bronx", "the bronx": "Bronx",
        "staten island": "Staten Island", "richmond": "Staten Island",
    }
    return aliases.get(b, raw.strip().title() if b in BOROUGHS else "")


def _terms_index():
    """[(subcategory_id, category_id, [terms...])] plus category-level terms."""
    subs = query(
        "SELECT s.id, s.category_id, s.search_terms_json FROM subcategories s WHERE s.retired = 0"
    )
    out = []
    for s in subs:
        terms = [t.lower() for t in json.loads(s["search_terms_json"])]
        if terms:
            out.append((s["id"], s["category_id"], terms))
    return out


def categorize(title: str, description: str, hint: str = "") -> tuple[int | None, int | None]:
    """(category_id, subcategory_id) for an event. The adapter's hint (a
    category or subcategory slug) wins; otherwise the subcategory whose
    search terms match the text most. No match → (None, None): the event
    goes to the admin's uncategorized queue rather than being dropped."""
    if hint:
        sub = query(
            "SELECT s.id, s.category_id FROM subcategories s"
            " JOIN categories c ON c.id = s.category_id"
            " WHERE ? IN (s.slug, c.slug || '/' || s.slug)",
            (hint,),
            one=True,
        )
        if sub:
            return sub["category_id"], sub["id"]
        cat = query("SELECT id FROM categories WHERE slug = ?", (hint,), one=True)
        if cat:
            return cat["id"], None

    text = f"{title} {description}".lower()
    best, best_score = None, 0
    for sub_id, cat_id, terms in _terms_index():
        score = sum(1 for t in terms if t in text)
        if score > best_score:
            best, best_score = (cat_id, sub_id), score
    return best if best else (None, None)


def _cross_source_duplicate(dedupe_key: str, source_id: int, tier: int):
    """An active event with the same dedupe key from another source.
    Returns 'skip' (theirs is better provenance) or the row to replace."""
    row = query(
        "SELECT id, tier FROM events WHERE dedupe_key = ? AND source_id != ?"
        " AND status = 'active'",
        (dedupe_key, source_id),
        one=True,
    )
    if not row:
        return None
    return "skip" if row["tier"] <= tier else row


def upsert_event(source, raw: RawEvent) -> str:
    """Insert or refresh one event. Returns 'inserted' | 'updated' | 'skipped'."""
    date_part = (raw.starts_at or "")[:10]
    dedupe_key = f"{slugify(raw.title)}:{date_part}"

    dupe = _cross_source_duplicate(dedupe_key, source["id"], source["tier"])
    if dupe == "skip":
        return "skipped"
    if dupe is not None:
        # We have better provenance — retire the higher-tier copy.
        execute("UPDATE events SET status = 'removed' WHERE id = ?", (dupe["id"],))

    cat_id, sub_id = categorize(raw.title, raw.description, raw.category_hint)
    existed = bool(
        query(
            "SELECT 1 FROM events WHERE source_id = ? AND external_id = ?",
            (source["id"], raw.external_id),
            one=True,
        )
    )
    db = get_db()
    db.execute(
        "INSERT INTO events (source_id, external_id, dedupe_key, title, description,"
        " url, starts_at, ends_at, venue_name, address, borough, city, lat, lon,"
        " category_id, subcategory_id, cost_cents, is_free, tier, raw_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (source_id, external_id) DO UPDATE SET"
        "  title = excluded.title, description = excluded.description,"
        "  url = excluded.url, starts_at = excluded.starts_at, ends_at = excluded.ends_at,"
        "  venue_name = excluded.venue_name, address = excluded.address,"
        "  borough = excluded.borough, cost_cents = excluded.cost_cents,"
        "  is_free = excluded.is_free, dedupe_key = excluded.dedupe_key,"
        "  status = CASE WHEN events.status = 'expired' THEN 'active' ELSE events.status END",
        (
            source["id"], raw.external_id, dedupe_key, raw.title.strip()[:200],
            strip_html(raw.description)[:2000], raw.url, raw.starts_at, raw.ends_at,
            raw.venue_name.strip()[:150], raw.address.strip()[:250],
            normalise_borough(raw.borough), current_app.config["CITY"],
            raw.lat, raw.lon, cat_id, sub_id, raw.cost_cents,
            1 if (raw.is_free or raw.cost_cents == 0) else 0,
            source["tier"], json.dumps(raw.raw)[:4000],
        ),
    )
    db.commit()
    return "updated" if existed else "inserted"


def run_source(source) -> dict:
    """Fetch one source through its adapter and upsert everything usable.
    Adapter errors count toward auto-disable (>= 5 in a row); a clean run
    resets the counter."""
    from .registry import adapter_for

    counts = {"inserted": 0, "updated": 0, "skipped": 0, "invalid": 0}
    try:
        adapter = adapter_for(source["kind"])
        config = json.loads(source["config_json"])
        for raw in adapter.fetch(config):
            if not (raw.title and raw.url and raw.starts_at):
                counts["invalid"] += 1
                continue
            counts[upsert_event(source, raw)] += 1
        execute(
            "UPDATE sources SET consecutive_errors = 0, last_run_at = datetime('now'),"
            " last_status = ? WHERE id = ?",
            (json.dumps(counts), source["id"]),
        )
    except Exception as exc:  # noqa: BLE001 — unattended cron must not die
        errors = source["consecutive_errors"] + 1
        disable = errors >= 5
        execute(
            "UPDATE sources SET consecutive_errors = ?, last_run_at = datetime('now'),"
            " last_status = ?, enabled = CASE WHEN ? THEN 0 ELSE enabled END WHERE id = ?",
            (errors, f"error: {exc}"[:300], disable, source["id"]),
        )
        log.warning("ingest: source %s failed (%d in a row%s): %s",
                    source["name"], errors, ", auto-disabled" if disable else "", exc)
        counts["error"] = str(exc)
    return counts
