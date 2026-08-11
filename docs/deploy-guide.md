# Deploy guide

Answers to every question `deploy/install.sh` asks, plus the follow-up jobs:
self-hosting SearXNG, going public, and turning email on. The installer itself
is one command:

```bash
git clone https://github.com/kylestrougo/onto.git ~/onto
cd ~/onto
bash deploy/install.sh
```

Re-running it after a `git pull` is the update procedure. It keeps your config.

---

## The installer's questions

### Admin username

The account **you** will sign up with in the browser afterwards. The first
signup with this exact name gets the admin role (taxonomy, weights, event
sources, model chain). Default is your Linux username — fine to accept.

### Public URL

**Press Enter and take the default (`http://localhost:5000`).** It only
matters once the app is reachable from outside the Pi — it feeds the links in
email notes and the cookie Secure flag. Change it later when you go public
(see below); guessing an https URL before it exists breaks login, because
Secure cookies are never sent over plain http.

### OpenRouter API key

Powers the two LLM features: tier-3 event research and the prose in weekly
notes. Free to get at <https://openrouter.ai/keys> (the app defaults to
`:free` models, so there's no spend). **Skippable**: without it, discovery
still runs on open-data and venue feeds, and notes use the built-in template
prose. Add it later by editing `OPENROUTER_API_KEY=` in `backend/.env` and
`sudo systemctl restart onto`.

### SearXNG URL

For tier-3 LLM web research. **Skip it on first install** — it's the most
optional piece of the whole stack, and tiers 1-2 (NYC Open Data, venue
feeds) keep event discovery alive without it. When you want it, the
walkthrough below takes about ten minutes; then set
`ONTO_SEARXNG_URL=http://127.0.0.1:8888` in `backend/.env` and restart.

### Email (Gmail address + app password)

The **sending** account for weekly notes — one-time admin config; each user
later chooses their own receiving address in Settings. Needs a Gmail
**app password** (not your real password): enable 2-step verification, then
create one at <https://myaccount.google.com/apppasswords>. Skippable — notes
then stay in-app only, which is also the reliable channel on iPhones.

Mail always starts in **dry-run**: notes are written to the log instead of
sent. See "Turning email on" below.

---

## Self-hosting SearXNG

SearXNG is a metasearch engine; onto queries it over its JSON API to find
pages for the LLM to extract events from. Run it on the same Pi, bound to
localhost — nothing about it needs to be public.

**A note on memory first:** SearXNG wants ~200-300MB. On a Pi with 2GB+
that's nothing; on a 1GB Pi 3 sharing with onto it's tight but workable —
check `free -m` after a day. If it's swapping, drop SearXNG; onto degrades
gracefully.

### 1. Install Docker (once)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# log out and back in so the group applies
```

### 2. Run SearXNG, localhost-only

```bash
sudo mkdir -p /opt/searxng
docker run -d --name searxng \
  -p 127.0.0.1:8888:8080 \
  -v /opt/searxng:/etc/searxng \
  -e SEARXNG_BASE_URL=http://127.0.0.1:8888/ \
  --restart unless-stopped \
  searxng/searxng
```

`127.0.0.1:8888` means only this machine can reach it — no auth needed, no
exposure.

### 3. The two settings that actually matter

The first run writes `/opt/searxng/settings.yml`. Two defaults will silently
break onto's research and MUST change:

- **JSON is off by default.** Onto calls the API with `format=json`; SearXNG
  answers 403 until `json` is added to the allowed formats.
- **The bot limiter is on by default.** It exists to protect public
  instances; on a localhost instance it just blocks your own API calls.

Edit `/opt/searxng/settings.yml` (sudo) so these sections read:

```yaml
use_default_settings: true

server:
  limiter: false
  secret_key: "put-a-long-random-string-here"   # python3 -c 'import secrets; print(secrets.token_hex(32))'

search:
  formats:
    - html
    - json
```

Then:

```bash
docker restart searxng
```

### 4. Verify, then wire it up

```bash
curl -s 'http://127.0.0.1:8888/search?q=test&format=json' | head -c 200
```

You should see JSON (`{"query": "test", ...}`), **not** a 403 page. Then:

```bash
# in ~/onto/backend/.env
ONTO_SEARXNG_URL=http://127.0.0.1:8888
```

```bash
sudo systemctl restart onto
cd ~/onto/backend && .venv/bin/flask --app wsgi:build research   # try one run by hand
```

Watch what it found (and what the validator dropped) in the output and in
`~/onto/logs/research.log` once cron takes over (Tuesdays and Fridays). Every
tier-3 event must survive the hard validator — future date, known borough,
URL answering 200 — so a low yield on the first run is normal, not broken.

---

## Going public (Tailscale Funnel)

When you want the app on your phone and your friends' phones:

```bash
# on the Pi, with Tailscale installed and logged in:
sudo tailscale funnel --bg 5000
```

It prints your public URL (`https://<pi-name>.<tailnet>.ts.net`). Then set
**both** of these together in `backend/.env` — an https URL with insecure
cookies leaks sessions; Secure cookies without https means nobody can log in:

```
ONTO_PUBLIC_URL=https://<pi-name>.<tailnet>.ts.net
ONTO_COOKIE_SECURE=1
```

```bash
sudo systemctl restart onto
```

Cloudflare Tunnel or any other https reverse proxy works the same way —
whatever URL people type in a browser is the value.

---

## Turning email on

1. Make sure `ONTO_SMTP_USER` / `ONTO_SMTP_PASSWORD` are set in
   `backend/.env` (the installer asked; add them by hand if you skipped).
2. In the app: Settings → "Your weekly note" → channel **Email** or **Both**,
   your address, day and hour. Set frequency to **daily** temporarily so you
   don't wait a week for the test.
3. Force one note and read it in the log (dry-run prints instead of sending):

   ```bash
   cd ~/onto/backend && .venv/bin/flask --app wsgi:build send-digests
   grep -A20 'DRY RUN' ~/onto/logs/digests.log | tail -40
   ```

4. Looks right? Flip `ONTO_MAIL_DRY_RUN=0` in `backend/.env`,
   `sudo systemctl restart onto`, and put your frequency back to weekly.

---

## Living with it

| What | Where |
|---|---|
| Web app service | `journalctl -u onto -f` |
| Cron job output | `~/onto/logs/*.log` (rotated weekly) |
| Install/update runs | `~/onto/deploy/logs/install-*.log` |
| Nightly DB backups | `~/onto/backups/` (14 days kept) |
| Update everything | `cd ~/onto && git pull && bash deploy/install.sh` |
| Model chain health | `/admin/models` in the app, or `~/onto/logs/chain.log` |
| Corpus & sources health | `/admin/sources` and `/admin/events` |

**Symptom-keyed quick fixes:**

- *Nobody can log in after going public* → `ONTO_PUBLIC_URL` and
  `ONTO_COOKIE_SECURE` weren't changed together. Fix both, restart.
- *No event suggestions* → sources added at `/admin/sources`? `flask ingest`
  run at least once? Goals have the "find me events" toggle on? Users have
  boroughs set (or none = all)?
- *Research finds nothing* → `curl` the SearXNG JSON check above; 403 means
  `formats`/`limiter` in settings.yml weren't changed.
- *Notes never send* → user's channel/address in Settings, `ONTO_MAIL_DRY_RUN`
  still `1`, or check `~/onto/logs/digests.log` for the SMTP error.
- *LLM prose missing from notes* → template fallback is doing its job while
  models fail; check `/admin/models` for the chain's health, or wait for the
  twice-weekly `refresh-chain` to repair it.
