#!/usr/bin/env bash
# Boot Onto exactly as systemd will, prove it serves, then stop it again.
#
#   bash deploy/smoke.sh
#
# Nothing is installed and nothing keeps running: the server is started in the
# background, checked, and killed on the way out. Run this before enabling the
# systemd unit — a failure here is far easier to read than a restart loop.
#
# It deliberately makes no call to OpenRouter. That costs quota and deserves to
# be watched in a browser, so it happens later at /admin.

set -uo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
venv="$repo/backend/.venv"
port="${PORT:-5000}"
base="http://127.0.0.1:$port"
fails=0

ok()   { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; fails=$((fails+1)); }
note() { printf 'INFO  %s\n' "$1"; }

[ -x "$venv/bin/waitress-serve" ] || { echo "FAIL  no venv at $venv — create it and pip install -r backend/requirements.txt first"; exit 1; }
[ -f "$repo/backend/.env" ]       || { echo "FAIL  no backend/.env — run deploy/make-env.sh first"; exit 1; }

if ss -tln 2>/dev/null | grep -qE "[:.]$port\b"; then
  echo "FAIL  port $port is already in use — stop whatever holds it, or PORT=5001 bash deploy/smoke.sh"
  exit 1
fi

cleanup() {
  if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    note "server stopped"
  fi
}
trap cleanup EXIT INT TERM

log="$(mktemp)"
cd "$repo/backend"
"$venv/bin/waitress-serve" --listen="127.0.0.1:$port" --threads=4 --call wsgi:build >"$log" 2>&1 &
pid=$!

# Boot on a Pi is a couple of seconds; give it room but fail fast if the
# process dies outright.
for _ in $(seq 1 40); do
  if ! kill -0 "$pid" 2>/dev/null; then
    bad "server exited during startup"
    echo "──── output ────"; cat "$log"; rm -f "$log"; exit 1
  fi
  curl -fsS -o /dev/null "$base/healthz" 2>/dev/null && break
  sleep 0.5
done

code() { curl -s -o /dev/null -w '%{http_code}' "$1"; }

[ "$(code "$base/healthz")" = "200" ] && ok "/healthz responds" || bad "/healthz did not respond"

# A signed-out visit to the home screen should redirect to the login page —
# anything else means the auth stack or templates did not wire up.
home="$(code "$base/")"
case "$home" in
  302) ok "/ redirects signed-out visitors to login" ;;
  *)   bad "/ returned $home (expected 302 to /login)" ;;
esac

if curl -fsS "$base/login" 2>/dev/null | grep -qi 'onto'; then
  ok "login page renders"
else
  bad "login page did not render"
fi

[ "$(code "$base/static/css/onto.css")" = "200" ] && ok "static assets served" || bad "static assets not served"
[ "$(code "$base/static/js/vendor/htmx.min.js")" = "200" ] && ok "htmx vendored and served" || bad "htmx not served"

# Signed-out, every app page must bounce to login — a 500 here means a
# blueprint or template broke.
for path in /week/2026-W33 /library /discover /feed /friends /settings /digests /month/2026-08; do
  c="$(code "$base$path")"
  [ "$c" = "302" ] && ok "$path redirects signed-out ($c)" || bad "$path returned $c (expected 302)"
done

db="$(grep -E '^ONTO_DB=' "$repo/backend/.env" | cut -d= -f2-)"
if [ -f "$db" ]; then
  ok "database created at $db"
  note "size: $(du -h "$db" | cut -f1)"
else
  bad "no database at $db"
fi

rss=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
[ -n "$rss" ] && note "resident memory: $((rss/1024))MB (unit caps at 200MB)"

if [ -s "$log" ]; then echo "──── server log ────"; sed 's/^/      /' "$log"; fi
rm -f "$log"

echo
if [ "$fails" -eq 0 ]; then
  echo "SMOKE PASSED — safe to install the systemd unit."
else
  echo "SMOKE FAILED ($fails) — fix the FAIL lines above before installing the unit."
fi
exit "$fails"
