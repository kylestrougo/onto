"""The week view — the home screen. Everything else is a detour (spec 11.7).

htmx pattern: every mutating endpoint returns the commitment card partial
(the server-rendered card is the source of truth, never the dragged DOM
clone) plus hx-swap-oob fragments for anything else the change touched —
the header progress badge, and the library chip appearing/disappearing.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import commitments, disclosure, goals, periods

bp = Blueprint("week", __name__)


@bp.get("/")
@login_required
def home():
    return redirect(
        url_for("week.show", key=periods.current_key("week", current_user.timezone))
    )


@bp.get("/week/<key>")
@login_required
def show(key: str):
    if not periods.valid_key("week", key):
        abort(404)
    current = periods.current_key("week", current_user.timezone)
    return render_template(
        "week.html",
        period_kind="week",
        key=key,
        label=periods.label("week", key),
        is_current=(key == current),
        prev_key=periods.shift("week", key, -1),
        next_key=periods.shift("week", key, 1),
        current_key=current,
        commitments=commitments.for_period(current_user.id, "week", key),
        available=commitments.uncommitted_goals(current_user.id, "week", key),
        progress=commitments.progress(current_user.id, "week", key),
    )


def _card(commitment_id: int, key: str, period_kind: str = "week", oob: bool = True):
    """A commitment card, with the out-of-band extras mutations must carry."""
    row = commitments.own_commitment(current_user.id, commitment_id)
    return render_template(
        "partials/_commitment_card.html",
        c=row,
        logged=commitments.logged_count(commitment_id),
        key=key,
        period_kind=period_kind,
        oob_progress=commitments.progress(current_user.id, period_kind, key) if oob else None,
    )


@bp.post("/week/<key>/commitments")
@login_required
def add(key: str):
    if not periods.valid_key("week", key):
        abort(404)
    goal = goals.own_goal(current_user.id, request.form.get("goal_id", type=int) or 0)
    if not goal:
        abort(404)

    target = request.form.get("target", type=int)
    due_date = request.form.get("due_date") or None
    cid, error = commitments.add(
        current_user.id, goal, "week", key, target=target, due_date=due_date
    )
    if error in ("needs_target", "needs_due_date"):
        # The drop needs one more fact (a target for "run more", a date for a
        # deadline). Render an inline prompt where the card would have gone;
        # submitting it re-posts here with the missing field filled.
        return render_template(
            "partials/_commitment_prompt.html",
            goal=goal,
            key=key,
            period_kind="week",
            needs=error.removeprefix("needs_"),
        )
    if error:
        # 200 on purpose: htmx only swaps 2xx, and the error is the content.
        return render_template("partials/_drop_error.html", message=error)
    return _card(cid, key)


def _own_or_404(commitment_id: int):
    row = commitments.own_commitment(current_user.id, commitment_id)
    if not row:
        abort(404)
    return row


@bp.post("/commitments/<int:cid>/log")
@login_required
def log(cid: int):
    c = _own_or_404(cid)
    # Guard one-shot kinds against logging past done (double-tap, htmx retry).
    if commitments.logged_count(cid) < (c["target"] or 1):
        commitments.log(current_user.id, c)
        for flag in disclosure.evaluate_after_completion(current_user.id):
            flash(disclosure.FLAGS[flag])
    return _card(cid, c["period_key"], c["period_kind"])


@bp.post("/commitments/<int:cid>/unlog")
@login_required
def unlog(cid: int):
    c = _own_or_404(cid)
    commitments.unlog(current_user.id, c)
    return _card(cid, c["period_key"], c["period_kind"])


@bp.delete("/commitments/<int:cid>")
@login_required
def remove(cid: int):
    c = _own_or_404(cid)
    goal = goals.own_goal(current_user.id, c["goal_id"])
    commitments.remove(c)
    # Empty swap removes the card; OOB fragments restore the library chip and
    # refresh the header count.
    return render_template(
        "partials/_removed.html",
        goal=goal,
        key=c["period_key"],
        period_kind=c["period_kind"],
        oob_progress=commitments.progress(current_user.id, c["period_kind"], c["period_key"]),
    )
