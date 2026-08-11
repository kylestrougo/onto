"""Settings: where you are (for event matching) and the show-everything
escape hatch for progressive disclosure."""
from __future__ import annotations

import json

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..db import execute, query
from ..notify import send as notify_send

bp = Blueprint("settings", __name__)

BOROUGHS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]


@bp.get("/settings")
@login_required
def edit():
    row = query("SELECT * FROM users WHERE id = ?", (current_user.id,), one=True)
    return render_template(
        "settings.html",
        boroughs=BOROUGHS,
        chosen=set(json.loads(row["boroughs_json"] or "[]")),
        show_everything=bool(row["show_everything"]),
        timezone=row["timezone"],
        prefs=notify_send.prefs_for(current_user.id),
    )


@bp.post("/settings")
@login_required
def save():
    chosen = [b for b in request.form.getlist("boroughs") if b in BOROUGHS]
    show_everything = 1 if request.form.get("show_everything") else 0
    execute(
        "UPDATE users SET boroughs_json = ?, show_everything = ? WHERE id = ?",
        (json.dumps(chosen), show_everything, current_user.id),
    )
    tz = (request.form.get("timezone") or "").strip()
    if tz:
        execute("UPDATE users SET timezone = ? WHERE id = ?", (tz, current_user.id))

    # Weekly note preferences (spec 9.3: the user controls frequency).
    notify_send.ensure_prefs(current_user.id)
    frequency = request.form.get("frequency")
    channel = request.form.get("channel")
    email = (request.form.get("email") or "").strip()[:200]
    try:
        send_hour = min(23, max(0, int(request.form.get("send_hour") or 9)))
    except ValueError:
        send_hour = 9
    if frequency in ("off", "weekly", "daily") and channel in ("email", "inapp", "both"):
        execute(
            "UPDATE notification_prefs SET frequency = ?, channel = ?, email = ?,"
            " send_hour = ? WHERE user_id = ?",
            (frequency, channel, email, send_hour, current_user.id),
        )
    flash("Saved.")
    return redirect(url_for("settings.edit"))


@bp.get("/digests")
@login_required
def digests():
    """The in-app copies of the weekly notes — the reliable channel where
    iOS web push isn't (scope's open item, resolved this way on purpose)."""
    rows = query(
        "SELECT * FROM digests WHERE user_id = ? ORDER BY id DESC LIMIT 20",
        (current_user.id,),
    )
    execute(
        "UPDATE digests SET read_at = datetime('now') WHERE user_id = ? AND read_at IS NULL",
        (current_user.id,),
    )
    return render_template("digests.html", digests=rows)


@bp.get("/unsub/<token>")
def unsubscribe(token: str):
    """One-click stop from the email footer. No login: the token is the
    proof, and the failure mode of a guessed token is merely quiet."""
    row = query(
        "SELECT user_id FROM notification_prefs WHERE unsub_token = ?", (token,), one=True
    )
    if row:
        execute(
            "UPDATE notification_prefs SET frequency = 'off' WHERE user_id = ?",
            (row["user_id"],),
        )
    return render_template("unsub.html", found=bool(row))
