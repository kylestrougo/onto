#!/usr/bin/env bash
# One-command install/update for Onto on a Raspberry Pi (or any Debian-ish box).
#
#   bash deploy/install.sh
#
# Safe to re-run: the first run asks a few questions and sets everything up;
# later runs keep your config, refresh dependencies, and restart the service
# (i.e. re-running IS the update procedure after a `git pull`).
#
# What it does, in order:
#   1. sanity checks (never as root; python3; sudo available for steps 7-9)
#   2. system packages it needs (asks before apt-get)
#   3. python venv + dependencies
#   4. backend/.env — asks for your config on first run, keeps it after
#   5. database init (fail fast, not at first request)
#   6. smoke test — boots the app exactly as systemd will, checks, stops it
#   7. systemd service (paths rewritten for THIS user and THIS checkout)
#   8. cron jobs (old onto lines replaced, other crontab entries untouched)
#   9. logrotate
#
# Everything except your secrets is logged to deploy/logs/install-*.log —
# paste that file if something goes wrong. Secrets are typed with echo off
# and never written to the log.
set -euo pipefail

# ── plumbing ────────────────────────────────────────────────────────────
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO/backend/.env"
VENV="$REPO/backend/.venv"
LOG_DIR="$REPO/deploy/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/install-$(date +%Y%m%d-%H%M%S).log"

# Log everything we print; keep stdin attached to the terminal for prompts.
exec > >(tee -a "$LOG") 2>&1

say()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf 'OK    %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
die()  { printf 'STOP  %s\n\nFull log: %s\n' "$1" "$LOG" >&2; exit 1; }

trap 'printf "\nSomething failed. Full log: %s\n" "$LOG" >&2' ERR

# Ask with a default. ask VAR "Question" "default"
ask() {
  local var="$1" q="$2" def="${3:-}" answer
  if [ -n "$def" ]; then
    read -rp "$q [$def]: " answer </dev/tty
    printf -v "$var" '%s' "${answer:-$def}"
  else
    read -rp "$q: " answer </dev/tty
    printf -v "$var" '%s' "$answer"
  fi
}

# Ask for a secret: echo off, never logged. ask_secret VAR "Question"
ask_secret() {
  local var="$1" q="$2" answer
  read -rsp "$q (typing is hidden, enter to skip): " answer </dev/tty
  printf '\n'
  printf -v "$var" '%s' "$answer"
}

yes_no() {  # yes_no "Question" default(y|n)
  local q="$1" def="${2:-y}" answer
  read -rp "$q [$def]: " answer </dev/tty
  answer="${answer:-$def}"
  [[ "$answer" =~ ^[Yy] ]]
}

say "Onto installer — $(date -Is)"
echo "checkout: $REPO"
echo "log:      $LOG"

# ── 1. sanity ───────────────────────────────────────────────────────────
say "1/9 Sanity checks"
[ "$(id -u)" -ne 0 ] || die "Run this as your normal user, not root — it uses sudo only where needed."
command -v python3 >/dev/null || die "python3 is missing. sudo apt-get install python3 python3-venv, then re-run."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "python3 is older than 3.9 — too old for Flask 3."
ok "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
if command -v sudo >/dev/null; then ok "sudo available"; else warn "no sudo — steps 7-9 (service/cron/logrotate) will be skipped"; fi
if [ -f /proc/device-tree/model ]; then ok "board: $(tr -d '\0' </proc/device-tree/model)"; fi
mem_mb=$(( $(awk '/MemAvailable/ {print $2}' /proc/meminfo) / 1024 ))
if [ "$mem_mb" -lt 150 ]; then warn "only ${mem_mb}MB memory available — the service caps at 200MB; check what else is running"; else ok "available memory: ${mem_mb}MB"; fi

# ── 2. system packages ──────────────────────────────────────────────────
say "2/9 System packages"
missing=()
python3 -c 'import venv, ensurepip' 2>/dev/null || missing+=(python3-venv)
compgen -G "/usr/include/ffi.h" >/dev/null || compgen -G "/usr/include/*/ffi.h" >/dev/null \
  || missing+=(libffi-dev build-essential)   # argon2 may build from source on ARM
if [ "${#missing[@]}" -gt 0 ]; then
  echo "Needed: ${missing[*]}"
  if command -v sudo >/dev/null && yes_no "Install them with apt-get now?" y; then
    sudo apt-get update -qq && sudo apt-get install -y "${missing[@]}"
  else
    die "Install these first, then re-run: sudo apt-get install ${missing[*]}"
  fi
else
  ok "everything already present"
fi

# ── 3. venv + dependencies ──────────────────────────────────────────────
say "3/9 Python environment"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO/backend/requirements.txt"
ok "dependencies installed into backend/.venv"

# ── 4. configuration ────────────────────────────────────────────────────
say "4/9 Configuration"
if [ -f "$ENV_FILE" ]; then
  ok "backend/.env already exists — keeping it (delete it to reconfigure from scratch)"
  # One thing worth checking on updates:
  if ! grep -qE '^OPENROUTER_API_KEY=.+' "$ENV_FILE"; then
    warn "OPENROUTER_API_KEY is empty — event research and weekly notes will use the template fallback"
  fi
else
  echo "First run — a few questions. Enter accepts the [default]."
  echo
  ask ADMIN_USERNAME "Admin username (the account YOU will sign up with — first signup with this name becomes admin)" "$(whoami)"
  ask PUBLIC_URL "Public URL (keep the default until you've set up Tailscale/Cloudflare)" "http://localhost:5000"
  case "$PUBLIC_URL" in
    https://*) COOKIE_SECURE=1 ;;
    *)         COOKIE_SECURE=0
               [ "$PUBLIC_URL" = "http://localhost:5000" ] || warn "non-https public URL — cookies will not be marked Secure" ;;
  esac
  ask_secret OPENROUTER_KEY "OpenRouter API key (sk-or-v1-..., from openrouter.ai/keys; skippable — add later in backend/.env)"
  ask SEARXNG_URL "Self-hosted SearXNG URL for tier-3 event research (skippable)" ""
  echo
  echo "Email for the weekly notes (all skippable — notes then stay in-app only):"
  ask SMTP_USER "  Gmail address to send from" ""
  SMTP_PASSWORD=""
  if [ -n "$SMTP_USER" ]; then
    ask_secret SMTP_PASSWORD "  Gmail app password (16 chars, from myaccount.google.com/apppasswords)"
    SMTP_PASSWORD="${SMTP_PASSWORD// /}"   # Gmail displays it with spaces; strip them
    [ -z "$SMTP_PASSWORD" ] || [ "${#SMTP_PASSWORD}" -eq 16 ] || warn "app passwords are usually 16 characters — double-check it"
  fi

  SECRET="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  umask 077
  cat > "$ENV_FILE" <<EOF
# Generated by deploy/install.sh on $(date -Is). Never commit this file.
# Every variable is documented in backend/.env.example.

ONTO_SECRET_KEY=$SECRET
ONTO_DB=$REPO/onto.db
ONTO_UPLOAD_DIR=$REPO/uploads
ONTO_PUBLIC_URL=$PUBLIC_URL
ONTO_COOKIE_SECURE=$COOKIE_SECURE

OPENROUTER_API_KEY=$OPENROUTER_KEY
ONTO_MODEL_CHAIN=meta-llama/llama-3.3-70b-instruct:free,google/gemma-2-9b-it:free,mistralai/mistral-7b-instruct:free

ONTO_SIGNUP_CAP_IP=5
ONTO_DAILY_CAP_USER=300

ONTO_ADMIN_USERNAME=$ADMIN_USERNAME

# Mail starts in dry-run: notes are logged, not sent. Flip to 0 once a test
# note looks right in the logs.
ONTO_MAIL_DRY_RUN=1
ONTO_SMTP_HOST=smtp.gmail.com
ONTO_SMTP_PORT=587
ONTO_SMTP_USER=$SMTP_USER
ONTO_SMTP_PASSWORD=$SMTP_PASSWORD
ONTO_MAIL_FROM=$SMTP_USER
ONTO_MAIL_FROM_NAME=Onto
ONTO_DEFAULT_TZ=America/New_York

ONTO_SEARXNG_URL=$SEARXNG_URL
ONTO_CITY=NYC
EOF
  chmod 600 "$ENV_FILE"
  ok "wrote backend/.env (mode 600)"
fi
mkdir -p "$REPO/logs" "$REPO/uploads" "$REPO/backups"

# ── choose the port ─────────────────────────────────────────────────────
say "Port"
port_busy() { ss -tln 2>/dev/null | grep -qE "[:.]$1\b"; }
ONTO_PORT=""
if [ -f /etc/systemd/system/onto.service ]; then
  ONTO_PORT="$(grep -oE -- '--listen=127\.0\.0\.1:[0-9]+' /etc/systemd/system/onto.service | grep -oE '[0-9]+$' || true)"
  [ -z "$ONTO_PORT" ] || ok "keeping the installed service's port: $ONTO_PORT"
fi
if [ -z "$ONTO_PORT" ]; then
  if port_busy 5000; then
    warn "port 5000 is taken by another service (curio, maybe?) — Onto needs its own"
    while :; do
      ask ONTO_PORT "Which port should Onto listen on?" "5001"
      [[ "$ONTO_PORT" =~ ^[0-9]+$ ]] || { warn "numbers only"; continue; }
      port_busy "$ONTO_PORT" && { warn "port $ONTO_PORT is taken too"; continue; }
      break
    done
  else
    ONTO_PORT=5000
  fi
  ok "Onto will listen on 127.0.0.1:$ONTO_PORT"
fi

# ── 5. database ─────────────────────────────────────────────────────────
say "5/9 Database"
( cd "$REPO/backend" && "$VENV/bin/flask" --app wsgi:build init-db )
ok "schema in place (existing data untouched)"

# ── 6. smoke test ───────────────────────────────────────────────────────
say "6/9 Smoke test"
SMOKE_PORT="$ONTO_PORT"
if systemctl is-active --quiet onto 2>/dev/null || port_busy "$ONTO_PORT"; then
  # The running service (or something else) holds the port; test on a spare
  # one instead of fighting it.
  SMOKE_PORT=$((ONTO_PORT + 1000))
  while port_busy "$SMOKE_PORT"; do SMOKE_PORT=$((SMOKE_PORT + 1)); done
fi
PORT="$SMOKE_PORT" bash "$REPO/deploy/smoke.sh" || die "smoke test failed — fix the FAIL lines above (full log: $LOG)"

# ── 7-9 need sudo ───────────────────────────────────────────────────────
if ! command -v sudo >/dev/null; then
  warn "no sudo — skipping service, cron, and logrotate. Run the app manually with:"
  echo "  cd $REPO/backend && $VENV/bin/waitress-serve --listen=127.0.0.1:5000 --threads=4 --call wsgi:build"
  exit 0
fi

# ── 7. systemd service ──────────────────────────────────────────────────
say "7/9 systemd service"
# The unit in the repo assumes user 'io' at /home/io/onto; rewrite for here.
UNIT_TMP="$(mktemp)"
sed -e "s|/home/io/onto|$REPO|g" \
    -e "s|^User=.*|User=$(whoami)|" \
    -e "s|^Group=.*|Group=$(id -gn)|" \
    -e "s|127\.0\.0\.1:5000|127.0.0.1:$ONTO_PORT|" \
    "$REPO/deploy/onto.service" > "$UNIT_TMP"
sudo cp "$UNIT_TMP" /etc/systemd/system/onto.service
rm -f "$UNIT_TMP"
sudo systemctl daemon-reload
sudo systemctl enable onto >/dev/null 2>&1
sudo systemctl restart onto
sleep 3
if systemctl is-active --quiet onto; then
  ok "service running (journalctl -u onto -f to watch it)"
else
  sudo systemctl status onto --no-pager || true
  die "service failed to start — status above, full log: $LOG"
fi

# ── 8. cron ─────────────────────────────────────────────────────────────
say "8/9 Cron jobs"
# Rewrite paths, drop comment/blank lines, replace any previous onto lines,
# and keep everything else already in the user's crontab.
CRON_TMP="$(mktemp)"
{ crontab -l 2>/dev/null | grep -vE "$REPO|/home/io/onto" | grep -v '^# onto jobs' || true; } > "$CRON_TMP"
echo "# onto jobs (managed by deploy/install.sh — edits below this line are replaced on re-run)" >> "$CRON_TMP"
sed -e "s|/home/io/onto|$REPO|g" "$REPO/deploy/crontab.example" \
  | grep -vE '^\s*(#|$|[A-Z_]+=)' \
  | sed -e "s|cd \$ONTO|cd $REPO/backend|" \
        -e "s|\$FLASK|$VENV/bin/flask --app wsgi:build|" \
        -e "s|$REPO/deploy/backup.sh|ONTO_DB=$REPO/onto.db ONTO_BACKUP_DIR=$REPO/backups $REPO/deploy/backup.sh|" \
  >> "$CRON_TMP"
crontab "$CRON_TMP"
rm -f "$CRON_TMP"
ok "installed $(crontab -l | grep -c "$REPO") onto cron lines (crontab -l to inspect)"

# ── 9. logrotate ────────────────────────────────────────────────────────
say "9/9 logrotate"
ROT_TMP="$(mktemp)"
sed -e "s|/home/io/onto|$REPO|g" -e "s|create 0640 io io|create 0640 $(whoami) $(id -gn)|" \
    "$REPO/deploy/logrotate.onto" > "$ROT_TMP"
sudo cp "$ROT_TMP" /etc/logrotate.d/onto
rm -f "$ROT_TMP"
ok "cron logs rotate weekly"

# ── done ────────────────────────────────────────────────────────────────
say "Done"
ADMIN_NAME="$(grep -E '^ONTO_ADMIN_USERNAME=' "$ENV_FILE" | cut -d= -f2-)"
cat <<EOF

Onto is running at http://127.0.0.1:$ONTO_PORT

Next steps, in order:
  1. Open it and SIGN UP as '$ADMIN_NAME' — the first signup with that
     name becomes the admin.
  2. Visit /admin/sources and add event sources (ready-made configs in
     docs/sources.md), then run one ingest by hand to see events arrive:
       cd $REPO/backend && $VENV/bin/flask --app wsgi:build ingest
  3. When you want it reachable from your phone:
       sudo tailscale funnel --bg $ONTO_PORT
     then set ONTO_PUBLIC_URL=https://<your-ts-name>.ts.net and
     ONTO_COOKIE_SECURE=1 in backend/.env and: sudo systemctl restart onto
  4. Email notes: they start in dry-run (logged, not sent). Watch one with
       grep -A5 'DRY RUN' $REPO/logs/digests.log
     and flip ONTO_MAIL_DRY_RUN=0 in backend/.env when it looks right.

Updating later:  cd $REPO && git pull && bash deploy/install.sh
Watching it:     journalctl -u onto -f     and    ls $REPO/logs/
This install's log: $LOG
EOF
