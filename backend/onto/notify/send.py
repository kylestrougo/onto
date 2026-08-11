"""Delivery: render the digest from verified facts + validated prose, then
send by email and/or store in-app. Cron fires hourly and dumbly; _is_due
decides per user, on the user's own clock (curio's email pattern).
"""
from __future__ import annotations

import json
import logging
import secrets
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app, render_template

from .. import periods
from ..db import execute, query
from . import compose

log = logging.getLogger(__name__)


def ensure_prefs(user_id: int) -> None:
    execute(
        "INSERT OR IGNORE INTO notification_prefs (user_id, unsub_token) VALUES (?, ?)",
        (user_id, secrets.token_urlsafe(24)),
    )


def prefs_for(user_id: int):
    ensure_prefs(user_id)
    return query(
        "SELECT * FROM notification_prefs WHERE user_id = ?", (user_id,), one=True
    )


def _is_due(prefs, local_now: datetime) -> bool:
    """Frequency + hour gate + once-a-day guard, all on the user's clock.

    The hour gate is >= on purpose: the cron is hourly, and "at or after"
    means a missed run delays the note an hour instead of dropping the day.
    That also covers DST's missing spring-forward hour; the repeated
    fall-back hour can't double-send because last_sent_on already guards it.
    """
    today = local_now.strftime("%Y-%m-%d")
    if prefs["last_sent_on"] == today:
        return False
    if local_now.hour < prefs["send_hour"]:
        return False
    freq = prefs["frequency"]
    if freq == "off":
        return False
    if freq == "weekly" and local_now.weekday() != prefs["send_dow"]:
        return False
    return True


def render_digest(user_row, facts: dict, prose: dict) -> tuple[str, str, str]:
    """(subject, text, html). Facts print from DB rows; prose is decoration."""
    events_by_id = {s["event_id"]: s for s in facts["suggestions"]}
    picks = [
        {"event": events_by_id[p["event_id"]], "why": p["why"]}
        for p in prose["event_picks"]
        if p["event_id"] in events_by_id
    ]
    notes_by_commitment = {n["commitment_id"]: n["text"] for n in prose["goal_notes"]}
    ctx = {
        "unsub_token": prefs_for(user_row["id"])["unsub_token"],
        "username": user_row["username"],
        "facts": facts,
        "prose": prose,
        "picks": picks,
        "notes_by_commitment": notes_by_commitment,
        "public_url": current_app.config["PUBLIC_URL"],
    }
    subject = f"Your week — {facts['week_label']}"
    html = render_template("email/digest.html", **ctx)
    text = render_template("email/digest.txt", **ctx)
    return subject, text, html


def _send_email(to_email: str, subject: str, text: str, html_body: str) -> bool:
    cfg = current_app.config
    if cfg["MAIL_DRY_RUN"]:
        log.info("[DRY RUN] would email %s: %s\n%s", to_email, subject, text)
        return True
    if not cfg["SMTP_USER"] or not cfg["SMTP_PASSWORD"]:
        log.error("SMTP not configured; skipping email to %s", to_email)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["MAIL_FROM_NAME"], cfg["MAIL_FROM"]))
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    try:
        _deliver(cfg["SMTP_HOST"], int(cfg["SMTP_PORT"]), cfg["SMTP_USER"],
                 cfg["SMTP_PASSWORD"], msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.error("failed to send to %s: %s", to_email, exc)
        return False


def _deliver(host: str, port: int, user: str, password: str, msg) -> None:
    """Send over whichever transport the port implies.

    465 expects TLS from the first byte; 587 opens in clear and upgrades via
    STARTTLS. Calling starttls() on 465 hangs until timeout; plain 587
    without the upgrade would send the app password in the clear. Choosing
    off the port means a config that works elsewhere works here unchanged.
    """
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def send_due_digests(force_user_id: int | None = None) -> dict:
    """The hourly cron body. force_user_id skips the due-gate (admin test)."""
    now = datetime.now(timezone.utc)
    rows = query(
        "SELECT p.*, u.username, u.timezone, u.id AS uid FROM notification_prefs p"
        " JOIN users u ON u.id = p.user_id"
        + (" WHERE p.user_id = ?" if force_user_id else " WHERE p.frequency != 'off'"),
        (force_user_id,) if force_user_id else (),
    )
    sent = skipped = failed = 0
    for prefs in rows:
        local_now = now.astimezone(periods.safe_zone(prefs["timezone"]))
        if force_user_id is None and not _is_due(prefs, local_now):
            skipped += 1
            continue

        user_row = query("SELECT * FROM users WHERE id = ?", (prefs["uid"],), one=True)
        facts, prose = compose.compose(user_row)
        subject, text, html = render_digest(user_row, facts, prose)

        ok = True
        if prefs["channel"] in ("email", "both"):
            if prefs["email"]:
                ok = _send_email(prefs["email"], subject, text, html)
            else:
                log.info("digest: user %s wants email but has no address", prefs["uid"])
        if prefs["channel"] in ("inapp", "both") or not ok:
            # In-app is also the safety net when email bounces.
            execute(
                "INSERT INTO digests (user_id, kind, subject, html, body_text, context_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (prefs["uid"], prefs["frequency"], subject, html, text,
                 json.dumps({"facts": facts, "prose": prose}, default=str)),
            )
        if ok:
            # The user's local date, not UTC's — the guard reads their clock.
            execute(
                "UPDATE notification_prefs SET last_sent_on = ? WHERE user_id = ?",
                (local_now.strftime("%Y-%m-%d"), prefs["uid"]),
            )
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}
