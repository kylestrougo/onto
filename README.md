# onto

A weekly planner that tells you what's actually happening near you this week
that matches what you said you wanted to do — and lets you pull a friend
into it.

You keep a list of things you want to do ("work out 3 times", "see some live
music"). Each week you drag a few onto the week and check them off. The app
researches real nearby events on a schedule (open data, venue feeds, LLM web
research through SearXNG — every event keeps its source link), matches them
to your goals, and suggests: *"Rooftop Jazz, Saturday, free — matches 'see
some live music'. You and Griff both want this."* Friends, shared goals, a
weekly leaderboard, and an honest LLM-written weekly note round it out.

Built to run on a Raspberry Pi: Flask + Jinja + htmx (no build step), SQLite,
waitress, cron jobs, OpenRouter free models.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend
../.venv/bin/python -m pytest         # the whole suite, no network needed
../.venv/bin/python wsgi.py           # http://localhost:5000
```

Set `ONTO_COOKIE_SECURE=0` for local http. First signup matching
`ONTO_ADMIN_USERNAME` becomes the admin.

## Deploy (Pi)

One command, as your normal user (not root), from anywhere you cloned the repo:

```bash
bash deploy/install.sh
```

It asks a few questions on the first run (admin username, OpenRouter key,
optional email), then does everything: system packages, venv, config, database,
a smoke test, the systemd service, all nine cron jobs, and logrotate — with
paths rewritten for your user and checkout, and a full log in `deploy/logs/`.
Secrets are typed with echo off and never logged. **Re-running it is the update
procedure**: `git pull && bash deploy/install.sh` keeps your config, refreshes
dependencies, and restarts the service.

It finishes by printing your next steps: sign up as the admin username, add
event sources at `/admin/sources` (ready-made configs in `docs/sources.md`),
expose it with `sudo tailscale funnel --bg 5000` when ready, and flip email
out of dry-run once a logged test note looks right. Tier-3 research needs
`ONTO_SEARXNG_URL` pointing at a self-hosted SearXNG.

The individual pieces are still there if you want them: `deploy/preflight.sh`
(read-only "will this Pi work?"), `deploy/smoke.sh` (boot, check, stop),
`deploy/make-env.sh` (config only).

## Layout

- `backend/onto/` — the app: views (server-rendered + htmx partials), domain
  modules, `ingest/` (source adapters + pipeline), `discovery/` (matching +
  suggestions), `notify/` (digest compose/validate/send), `llm.py`
  (OpenRouter chain).
- `backend/tests/` — pytest; every file's docstring names the failure it
  prevents.
- `deploy/` — systemd unit, cron, backup, preflight, smoke.
- `docs/` — `decisions.md`, `sources.md`.
