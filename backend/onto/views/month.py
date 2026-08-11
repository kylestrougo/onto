"""The month view: window goals live here, and anything else can too."""
from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import commitments, feed, goals, mix, periods, scoring

bp = Blueprint("month", __name__)


@bp.get("/month")
@login_required
def home():
    return redirect(
        url_for("month.show", key=periods.current_key("month", current_user.timezone))
    )


@bp.get("/month/<key>")
@login_required
def show(key: str):
    if not periods.valid_key("month", key):
        abort(404)
    current = periods.current_key("month", current_user.timezone)
    return render_template(
        "period.html",
        period_kind="month",
        key=key,
        label=periods.label("month", key),
        is_current=(key == current),
        is_past=(key < current),
        prev_key=periods.shift("month", key, -1),
        next_key=periods.shift("month", key, 1),
        current_key=current,
        commitments=commitments.for_period(current_user.id, "month", key),
        available=commitments.uncommitted_goals(current_user.id, "month", key),
        progress=commitments.progress(current_user.id, "month", key),
        score=scoring.period_score(current_user.id, "month", key),
        mix_rows=mix.bar_data(current_user.id, "month", key),
    )


@bp.post("/month/<key>/commitments")
@login_required
def add(key: str):
    if not periods.valid_key("month", key):
        abort(404)
    goal = goals.own_goal(current_user.id, request.form.get("goal_id", type=int) or 0)
    if not goal:
        abort(404)
    cid, error = commitments.add(
        current_user.id,
        goal,
        "month",
        key,
        target=request.form.get("target", type=int),
        due_date=request.form.get("due_date") or None,
    )
    if error in ("needs_target", "needs_due_date"):
        return render_template(
            "partials/_commitment_prompt.html",
            goal=goal,
            key=key,
            period_kind="month",
            needs=error.removeprefix("needs_"),
        )
    if error:
        return render_template("partials/_drop_error.html", message=error)
    feed.record(current_user.id, "committed", goal_id=goal["id"], commitment_id=cid)
    from .week import _card

    return _card(cid, key, "month")
