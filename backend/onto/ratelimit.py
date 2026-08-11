"""Abuse guards for a publicly-reachable app with open signup.

Counters live in SQLite rather than memory so they survive a restart and are
visible to the cron jobs. Intentionally coarse — a daily bucket, not a token
bucket. It is a quota guard, not a DDoS defence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app, request

from .db import get_db


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def client_ip() -> str:
    """Real client IP behind a tunnel or reverse proxy.

    CF-Connecting-IP is set by Cloudflare and, when the Pi is only reachable
    through the tunnel, cannot be spoofed by an outside caller. X-Forwarded-For
    is honoured second for a plain local reverse proxy (Tailscale Funnel).
    """
    cf = request.headers.get("CF-Connecting-IP")
    if cf:
        return cf.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _bump(subject: str, day: str) -> int:
    db = get_db()
    # UPSERT returning the new value — one statement, no read-then-write race.
    cur = db.execute(
        "INSERT INTO usage_counters (subject, day, count) VALUES (?, ?, 1) "
        "ON CONFLICT(subject, day) DO UPDATE SET count = count + 1 "
        "RETURNING count",
        (subject, day),
    )
    used = cur.fetchone()[0]
    cur.close()
    db.commit()
    return used


def check_signup_rate() -> tuple[bool, str]:
    """Cap accounts created per IP per day."""
    cap = current_app.config["SIGNUP_CAP_IP"]
    used = _bump(f"signup:{client_ip()}", _today())
    if used > cap:
        return False, "Too many accounts created from here today."
    return True, ""


def prune_counters(keep_days: int = 7) -> int:
    """Housekeeping for the nightly cron — the table is otherwise unbounded."""
    db = get_db()
    cur = db.execute(
        "DELETE FROM usage_counters WHERE day < date('now', ?)",
        (f"-{int(keep_days)} days",),
    )
    n = cur.rowcount
    cur.close()
    db.commit()
    return n
