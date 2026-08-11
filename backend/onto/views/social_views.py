"""Friends, feed, profiles, leaderboard, shared-goal invites (spec 7)."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import feed, goals, periods, social
from ..db import query

bp = Blueprint("social", __name__)


@bp.get("/friends")
@login_required
def friends():
    return render_template(
        "friends.html",
        friends=social.friends_of(current_user.id),
        incoming=social.pending_for(current_user.id),
        outgoing=social.pending_from(current_user.id),
        goal_invites=social.invites_for(current_user.id),
    )


@bp.post("/friends/request")
@login_required
def request_friend():
    error = social.request_friend(current_user.id, request.form.get("username", ""))
    flash(error or "Sent. They'll see it on their friends page.")
    return redirect(url_for("social.friends"))


@bp.post("/friends/<int:fid>/accept")
@login_required
def accept_friend(fid: int):
    social.respond(current_user.id, fid, accept=True)
    return redirect(url_for("social.friends"))


@bp.post("/friends/<int:fid>/decline")
@login_required
def decline_friend(fid: int):
    social.respond(current_user.id, fid, accept=False)
    return redirect(url_for("social.friends"))


@bp.post("/friends/<int:other_id>/remove")
@login_required
def remove_friend(other_id: int):
    social.unfriend(current_user.id, other_id)
    flash("Removed.")
    return redirect(url_for("social.friends"))


@bp.get("/feed")
@login_required
def show_feed():
    week = periods.current_key("week", current_user.timezone)
    return render_template(
        "feed.html",
        items=feed.feed_for(current_user.id),
        board=feed.leaderboard(current_user.id, week),
        week_label=periods.label("week", week),
    )


@bp.get("/people/<username>")
@login_required
def profile(username: str):
    person = query("SELECT * FROM users WHERE username = ?", (username,), one=True)
    if not person:
        abort(404)
    if person["id"] == current_user.id:
        return redirect(url_for("library.index"))
    visible = feed.visible_goals(current_user.id, person["id"])
    week = periods.current_key("week", current_user.timezone)
    from .. import scoring

    score = scoring.period_score(person["id"], "week", week) if visible else None
    return render_template(
        "profile.html",
        person=person,
        goals=visible,
        score=score,
        friendship=social.friendship_between(current_user.id, person["id"]),
    )


@bp.post("/goals/<int:goal_id>/visibility")
@login_required
def set_visibility(goal_id: int):
    goal = goals.own_goal(current_user.id, goal_id)
    if not goal:
        abort(404)
    vis = request.form.get("visibility")
    if vis in ("private", "friends", "public"):
        from ..db import execute

        execute("UPDATE goals SET visibility = ? WHERE id = ?", (vis, goal_id))
    return redirect(request.form.get("back") or url_for("library.edit_form", goal_id=goal_id))


@bp.post("/goals/<int:goal_id>/invite")
@login_required
def invite(goal_id: int):
    goal = goals.own_goal(current_user.id, goal_id)
    if not goal:
        abort(404)
    error = social.invite_to_goal(current_user.id, goal_id, request.form.get("username", ""))
    if not error:
        feed.record(current_user.id, "goal_shared", goal_id=goal_id)
    flash(error or "Invited. It becomes a shared goal when they accept.")
    return redirect(url_for("library.edit_form", goal_id=goal_id))


@bp.post("/invites/<int:invite_id>/accept")
@login_required
def accept_invite(invite_id: int):
    goal_id = social.respond_invite(current_user.id, invite_id, accept=True)
    if goal_id:
        flash("You're in — it shows up in your list now, and you both check it off.")
    return redirect(url_for("social.friends"))


@bp.post("/invites/<int:invite_id>/decline")
@login_required
def decline_invite(invite_id: int):
    social.respond_invite(current_user.id, invite_id, accept=False)
    return redirect(url_for("social.friends"))
