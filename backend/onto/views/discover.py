"""Discovery surface: "For you" — real nearby events matched to what you
said you wanted to do. Accept puts it on your week with the link attached.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import goals
from ..discovery import matching, suggestions

bp = Blueprint("discover", __name__)


@bp.get("/discover")
@login_required
def index():
    rows = suggestions.pending_for(current_user.id, limit=10)
    cards = [
        {"s": row, "companions": suggestions.group_companions(row)} for row in rows
    ]
    suggestions.mark_seen(current_user.id)
    has_discovery_goals = bool(matching.discovery_goals(current_user.id))
    return render_template(
        "discover.html", cards=cards, has_discovery_goals=has_discovery_goals
    )


@bp.post("/suggestions/<int:sid>/accept")
@login_required
def accept(sid: int):
    error = suggestions.accept(current_user, sid)
    if error:
        flash(error)
    else:
        flash("On your week — the link's on the card.")
    return redirect(request.form.get("back") or url_for("discover.index"))


@bp.post("/suggestions/<int:sid>/dismiss")
@login_required
def dismiss(sid: int):
    suggestions.dismiss(current_user.id, sid)
    return redirect(request.form.get("back") or url_for("discover.index"))


@bp.post("/suggestions/<int:sid>/flag")
@login_required
def flag(sid: int):
    suggestions.flag(current_user.id, sid, request.form.get("reason", ""))
    flash("Thanks — that one's pulled, and we watch the source.")
    return redirect(request.form.get("back") or url_for("discover.index"))


@bp.post("/goals/<int:goal_id>/discovery")
@login_required
def toggle(goal_id: int):
    """The one toggle (spec 11.6): per goal, defaulted off."""
    goal = goals.own_goal(current_user.id, goal_id)
    if not goal:
        abort(404)
    from ..db import execute

    new_val = 0 if goal["discovery_enabled"] else 1
    execute("UPDATE goals SET discovery_enabled = ? WHERE id = ?", (new_val, goal_id))
    if new_val:
        flash("We'll look for real events nearby that fit this one.")
    else:
        flash("No more event suggestions for this one.")
    return redirect(request.form.get("back") or url_for("library.edit_form", goal_id=goal_id))
