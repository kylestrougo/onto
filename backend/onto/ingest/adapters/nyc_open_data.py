"""Tier 1: NYC Open Data (Socrata) datasets — parks events, event permits.

Generic over any Socrata JSON endpoint: the source's config maps dataset
fields onto RawEvent fields, so the admin can add a new dataset with a
source row instead of code.

config_json shape:
  {
    "url": "https://data.cityofnewyork.us/resource/xxxx-xxxx.json",
    "params": {"$limit": "500", "$order": "start_date_time"},
    "map": {                      # dataset field -> RawEvent field
      "external_id": "event_id",
      "title": "event_name",
      "starts_at": "start_date_time",
      "ends_at": "end_date_time",
      "venue_name": "event_location",
      "borough": "event_borough",
      "url_field": "event_website"          # optional per-row link
    },
    "event_url": "https://www.nycgovparks.org/events",   # fallback link
    "category_hint": "culture"                            # optional
  }
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .. import base
from ..base import RawEvent


def _iso(value: str) -> str:
    """Socrata floating timestamps ('2026-08-15T19:00:00.000') → our UTC-ish
    'YYYY-MM-DD HH:MM:SS'. Naive local NYC times are stored as-is: for
    same-city matching, wall time is the meaningful clock."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return ""


class NycOpenDataAdapter:
    def fetch(self, config: dict) -> Iterable[RawEvent]:
        mapping = config.get("map", {})
        resp = base.http_get(config["url"], params=config.get("params", {"$limit": "500"}))
        rows = resp.json()

        def get(row, key, default=""):
            return str(row.get(mapping.get(key, key), "") or default).strip()

        for row in rows:
            starts = _iso(get(row, "starts_at"))
            url = get(row, "url_field") or config.get("event_url", config["url"])
            ext_id = get(row, "external_id") or f"{get(row, 'title')}|{starts[:10]}"
            cost_raw = get(row, "cost")
            yield RawEvent(
                external_id=ext_id,
                title=get(row, "title"),
                url=url,
                starts_at=starts,
                ends_at=_iso(get(row, "ends_at")) or None,
                venue_name=get(row, "venue_name"),
                address=get(row, "address"),
                borough=get(row, "borough"),
                description=get(row, "description"),
                is_free=(cost_raw in ("", "0", "free", "Free")),
                category_hint=config.get("category_hint", ""),
                raw={k: row.get(k) for k in list(row)[:25]},
            )
