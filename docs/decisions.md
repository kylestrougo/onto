# Decisions

Design calls worth remembering, and the ones marked revisitable.

## Product

- **Vocabulary split.** The database says goal/commitment; the UI says
  "Things I want to do" / "This week" (spec 11.4). No user-facing mention of
  commitments, provenance, corpus, or tiers — provenance surfaces as
  "from NYC Parks" on a card.
- **Shared-goal scoring: full points to every member.** Any member's log
  counts toward shared progress; on completion, every member scores the full
  weight. Simplest defensible rule and it encourages doing things together.
  *Revisitable* if it inflates leaderboards.
- **Late deadline completions score zero but count in history.** The score
  rewards the deadline; the history rewards the doing.
- **Recurring deadline goals don't auto-materialise.** A repeating "by the
  20th" has no inferable next date; re-adding is a human call.
- **"Follow" collapsed to mutual friends.** One concept covers the feed and
  leaderboard for a friend-circle app.
- **Making a goal private erases its history from friends' feeds.** Feed
  visibility is checked at read time against the goal's *current* setting.
- **Browser push excluded on purpose** (scope open item): iOS PWA push is
  fragile. Email + the in-app notes page are the channels; the in-app copy is
  also the fallback when email bounces.

## Discovery integrity (spec 10)

- **Retrieval is a layer, not a prompt.** SearXNG finds pages, the server
  fetches and strips them, and the LLM only extracts from that text. It never
  browses. (Curio's documented lesson.)
- **Tier-3 hard validator at ingest:** parseable future date, recognised
  borough or none, and a URL that answers 200 — an event failing any of these
  is dropped and counted, never stored.
- **Digest order of operations:** verify (URL 200 + future) → compose
  (ids only) → validate (unknown id = rejected fragment; date/price/URL-shaped
  prose = rejected fragment) → render facts from DB rows. The LLM's words are
  decoration around server-printed facts, and the template fallback means a
  digest always sends.
- **Quarantine thresholds:** 5 consecutive adapter errors auto-disable a
  source; 3 distinct users' flags in 30 days quarantine it and all its active
  events. Unquarantining is admin-only and does not resurrect quarantined
  events.

## Technical

- **Period keys, not period rows.** Commitments carry `'2026-W33'` /
  `'2026-08'` computed in the user's timezone; `periods.py` is the only
  module that thinks about timezones.
- **Event times are stored as wall time** for tier-1 Socrata feeds (naive
  local NYC timestamps); `.ics` with TZID and JSON-LD with offsets are
  converted to UTC. For one-city matching, wall time is the meaningful clock.
  *Revisitable* the day this goes multi-city.
- **Scores compute on read.** A user-period is a handful of rows; a cache
  would be one more thing to invalidate.
- **Cron calls `flask <command>`; commands are due-gated and idempotent.**
  Hourly-and-dumb cron plus per-user due-gates (local clock, `last_sent_on`
  local date) self-heal after downtime and can't double-fire.
- **NYC-only, borough-grained location** for v1. Users pick boroughs;
  unknown-borough events stay eligible so decent sources aren't starved.
  A `city` column everywhere keeps multi-city open.
- **Progressive disclosure is one-way and persisted** (user_flags), with a
  "show everything" escape hatch in settings.
- **No build step anywhere.** htmx + SortableJS are vendored files; the Pi
  serves exactly what git holds.

## Known gaps (tracked, not forgotten)

- Privacy tooling (export, delete account) and moderation (report/block for
  public goals) from the scope's open items are not yet built — both gate
  opening the instance beyond friends.
- SearXNG runs as a separate service; if it's down, tiers 1-2 keep discovery
  alive and `flask research` logs a skip.
