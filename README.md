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

```bash
bash deploy/preflight.sh    # read-only: will this Pi work?
bash deploy/make-env.sh     # writes backend/.env (local-only, mail dry-run)
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
bash deploy/smoke.sh        # boots as systemd would, checks, stops
sudo cp deploy/onto.service /etc/systemd/system/ && sudo systemctl enable --now onto
crontab deploy/crontab.example   # after editing paths
sudo cp deploy/logrotate.onto /etc/logrotate.d/onto
```

TLS via Tailscale Funnel (`sudo tailscale funnel --bg 5000`) or your own
tunnel. Add event sources from `/admin/sources` — `docs/sources.md` has
ready-made configs. Tier-3 research needs `ONTO_SEARXNG_URL` pointing at a
self-hosted SearXNG.

## Layout

- `backend/onto/` — the app: views (server-rendered + htmx partials), domain
  modules, `ingest/` (source adapters + pipeline), `discovery/` (matching +
  suggestions), `notify/` (digest compose/validate/send), `llm.py`
  (OpenRouter chain).
- `backend/tests/` — pytest; every file's docstring names the failure it
  prevents.
- `deploy/` — systemd unit, cron, backup, preflight, smoke.
- `docs/` — `decisions.md`, `sources.md`.
