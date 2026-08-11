"""The goal library — "Things I want to do". Created once, reused forever."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import goals, seed

bp = Blueprint("library", __name__)


def _form_context(**extra):
    return {
        "taxonomy": goals.categories_with_subs(),
        **extra,
    }


@bp.get("/library")
@login_required
def index():
    rows = goals.library(current_user.id)
    active = [g for g in rows if not g["retired_at"]]
    retired = [g for g in rows if g["retired_at"]]
    have = {g["title"] for g in active}
    quick_options = [s[0] for s in seed.STARTER_GOALS if s[0] not in have]
    return render_template(
        "library.html",
        active=active,
        retired=retired,
        quick_options=quick_options,
        **_form_context(),
    )


@bp.post("/goals")
@login_required
def create():
    # The form asks "how many times?" instead of a kind quiz: 1 means a
    # once-is-done goal, more means countable. The special kinds live under
    # "More options" (and the old explicit values still work).
    kind = (request.form.get("kind") or "").strip()
    target = request.form.get("default_target", type=int)
    if not kind:
        kind = "countable" if (target or 1) > 1 else "binary"
    _, error = goals.create(
        current_user.id,
        title=request.form.get("title", ""),
        kind=kind,
        category_id=request.form.get("category_id", type=int) or 0,
        subcategory_id=request.form.get("subcategory_id", type=int),
        default_target=target,
        recurring=bool(request.form.get("recurring")),
        notes=request.form.get("notes", ""),
    )
    if error:
        flash(error)
    return redirect(request.form.get("back") or url_for("library.index"))


@bp.post("/goals/quick-add")
@login_required
def quick_add():
    title = (request.form.get("title") or "").strip()
    if seed.quick_add(current_user.id, title):
        flash(f'"{title}" is on your list.')
    return redirect(url_for("library.index"))


@bp.get("/goals/<int:goal_id>/edit")
@login_required
def edit_form(goal_id: int):
    goal = goals.own_goal(current_user.id, goal_id)
    if not goal:
        abort(404)
    return render_template("goal_edit.html", goal=goal, **_form_context())


@bp.post("/goals/<int:goal_id>/edit")
@login_required
def edit(goal_id: int):
    error = goals.update(
        current_user.id,
        goal_id,
        title=request.form.get("title", ""),
        category_id=request.form.get("category_id", type=int) or 0,
        subcategory_id=request.form.get("subcategory_id", type=int),
        default_target=request.form.get("default_target", type=int),
        recurring=bool(request.form.get("recurring")),
        notes=request.form.get("notes", ""),
    )
    if error:
        flash(error)
        return redirect(url_for("library.edit_form", goal_id=goal_id))
    return redirect(url_for("library.index"))


@bp.post("/goals/<int:goal_id>/retire")
@login_required
def retire(goal_id: int):
    goals.retire(current_user.id, goal_id)
    flash("Tucked away. Its history stays.")
    return redirect(url_for("library.index"))


@bp.post("/goals/<int:goal_id>/unretire")
@login_required
def unretire(goal_id: int):
    goals.unretire(current_user.id, goal_id)
    return redirect(url_for("library.index"))
