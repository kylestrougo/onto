"""Self-hosted SearXNG client (spec 8.2). Retrieval is its own layer:
search finds pages, fetching reads them, the LLM only ever extracts from
what was actually fetched — it never browses (spec 10.1).
"""
from __future__ import annotations

import logging

import requests
from flask import current_app

from .base import FETCH_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)


def search(query: str, limit: int = 5) -> list[dict]:
    """Top results as [{url, title}]. Empty on any failure — research is a
    best-effort cron job, not a user-facing request."""
    base_url = current_app.config["SEARXNG_URL"]
    if not base_url:
        return []
    try:
        resp = requests.get(
            f"{base_url}/search",
            params={"q": query, "format": "json", "safesearch": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("searxng query failed (%s): %s", query, exc)
        return []
    out = []
    for r in results[:limit]:
        url = r.get("url", "")
        if url.startswith("http"):
            out.append({"url": url, "title": r.get("title", "")})
    return out
