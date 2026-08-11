#!/usr/bin/env bash
# Read-only audit: will Onto deploy cleanly on this Pi?
#
#   bash deploy/preflight.sh
#
# Checks everything a deploy depends on and changes NOTHING. Every line is
# prefixed OK / WARN / STOP. Paste the whole output back if something fails.

set -uo pipefail

ok()   { printf 'OK    %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
stop() { printf 'STOP  %s\n' "$1"; }

echo "── Hardware / OS ──────────────────────────────────────────────"
arch="$(uname -m)"
case "$arch" in
  aarch64|armv7l|x86_64) ok "architecture: $arch" ;;
  *) warn "architecture: $arch (untested)" ;;
esac
[ -f /proc/device-tree/model ] && ok "board: $(tr -d '\0' </proc/device-tree/model)"
. /etc/os-release 2>/dev/null && ok "OS: $PRETTY_NAME"

echo
echo "── Memory / disk ──────────────────────────────────────────────"
mem_avail_kb="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
mem_avail_mb=$((mem_avail_kb / 1024))
if [ "$mem_avail_mb" -ge 250 ]; then ok "available memory: ${mem_avail_mb}MB (service caps at 200MB)"
elif [ "$mem_avail_mb" -ge 150 ]; then warn "available memory: ${mem_avail_mb}MB — tight; check what else is running"
else stop "available memory: ${mem_avail_mb}MB — not enough headroom for the 200MB unit cap"
fi
swap_kb="$(awk '/SwapTotal/ {print $2}' /proc/meminfo)"
[ "${swap_kb:-0}" -gt 0 ] && ok "swap present: $((swap_kb/1024))MB" || warn "no swap — a memory spike kills processes instead of slowing down"
disk_avail="$(df -Pm "$HOME" | awk 'NR==2 {print $4}')"
if [ "$disk_avail" -ge 1024 ]; then ok "free disk in \$HOME: ${disk_avail}MB"
else warn "free disk in \$HOME: ${disk_avail}MB — backups + logs want at least 1GB"
fi

echo
echo "── Python ─────────────────────────────────────────────────────"
if command -v python3 >/dev/null; then
  pyver="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  # Flask 3.0 needs >= 3.8; we develop on 3.11.
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    ok "python3 $pyver"
  else
    stop "python3 $pyver — Flask 3 needs at least 3.8, and this repo is tested on 3.11"
  fi
  python3 -c 'import venv' 2>/dev/null && ok "venv module present" || stop "python3-venv missing — sudo apt install python3-venv"
  python3 -c 'import ensurepip' 2>/dev/null && ok "ensurepip present" || stop "ensurepip missing — sudo apt install python3-venv"
else
  stop "no python3 on PATH"
fi
# argon2-cffi may compile from source on ARM if no wheel matches.
[ -f /usr/include/ffi.h ] && ok "libffi headers present" || warn "libffi-dev missing — argon2-cffi may fail to build: sudo apt install libffi-dev build-essential"

echo
echo "── Network / ports ────────────────────────────────────────────"
if ss -tln 2>/dev/null | grep -qE '[:.]5000\b'; then
  stop "port 5000 already in use: $(ss -tlnp 2>/dev/null | grep -E '[:.]5000\b' | head -1)"
else
  ok "port 5000 free"
fi
command -v tailscale >/dev/null && ok "tailscale installed ($(tailscale version 2>/dev/null | head -1))" || warn "tailscale not installed — needed for Funnel TLS exposure (or bring your own tunnel)"

echo
echo "── Existing state ─────────────────────────────────────────────"
[ -d "$HOME/onto" ] && warn "\$HOME/onto already exists — this may be a re-deploy" || ok "no prior install in \$HOME/onto"
systemctl is-active --quiet onto 2>/dev/null && warn "an onto.service is already running" || ok "no onto.service running"
crontab -l 2>/dev/null | grep -q onto && warn "crontab already mentions onto" || ok "crontab has no onto entries"

echo
echo "── Top memory consumers (for context) ─────────────────────────"
ps -eo rss,comm --sort=-rss 2>/dev/null | head -6 | awk 'NR>1 {printf "      %dMB  %s\n", $1/1024, $2}'

echo
echo "Preflight complete. Fix any STOP lines before deploying; WARNs are judgement calls."
