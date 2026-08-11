# Event sources

The corpus is fed by `flask ingest` (cron, every 6 hours) from rows in the
`sources` table, added from `/admin/sources`. All sources are free (spec 12.3).
Tiers: **1** official/open data, **2** structured venue feed, **3** LLM research.

## Ready-made configs

### NYC Parks events (tier 1, `nyc_open_data`)

NYC Open Data publishes Parks special events as a Socrata dataset. Find the
current dataset id at <https://data.cityofnewyork.us> (search "parks events");
the resource endpoint looks like `https://data.cityofnewyork.us/resource/<id>.json`.

```json
{
  "url": "https://data.cityofnewyork.us/resource/6v4b-5gp4.json",
  "params": {"$limit": "500"},
  "map": {
    "external_id": "event_id",
    "title": "title",
    "starts_at": "date",
    "venue_name": "location",
    "borough": "borough",
    "description": "snippet",
    "url_field": "link"
  },
  "event_url": "https://www.nycgovparks.org/events"
}
```

Field names differ per dataset — check the dataset's API docs and adjust `map`.
Any Socrata dataset with a start time works, including street event permits.

### A venue .ics feed (tier 2, `ics`)

Most box-office/calendar software (Squarespace, Tockify, Google Calendar)
exports `.ics`. Feeds rarely carry a borough, so the admin supplies it:

```json
{
  "url": "https://www.example-hall.org/calendar/export.ics",
  "borough": "Manhattan",
  "venue_name": "Example Hall",
  "category_hint": "culture/live-music"
}
```

### A venue page with JSON-LD (tier 2, `jsonld`)

Museums and theatres widely embed schema.org `Event` markup for SEO. Point the
adapter at the listings page:

```json
{
  "url": "https://www.example-museum.org/whats-on",
  "borough": "Brooklyn",
  "category_hint": "culture/museums-exhibits"
}
```

View-source and search for `application/ld+json` to confirm a page qualifies.

### LLM research (tier 3, `llm_research`)

Configured in Phase 6; needs `ONTO_SEARXNG_URL` set. No `url` in the config —
queries are built from the taxonomy's search terms.

## Operational notes

- 5 consecutive adapter failures auto-disable a source (visible in /admin/sources).
- User flags quarantine repeat-offender sources (spec 10.6).
- Events keyword-match against the taxonomy's search terms; misses land in the
  admin uncategorized queue rather than being dropped.
- Cross-source duplicates (same title slug + date) keep the lower tier number.
