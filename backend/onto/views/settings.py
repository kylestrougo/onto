"""Settings: where you are (for event matching) and the show-everything
escape hatch for progressive disclosure."""
from __future__ import annotations

import json

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..db import execute, query

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
    flash("Saved.")
    return redirect(url_for("settings.edit"))
