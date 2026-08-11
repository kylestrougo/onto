"""The goal library — "Things I want to do". Created once, reused forever."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import goals

bp = Blueprint("library", __name__)


def _form_context(**extra):
    return {
        "taxonomy": goals.categories_with_subs(),
        "kind_help": goals.KIND_HELP,
        **extra,
    }


@bp.get("/library")
@login_required
def index():
    rows = goals.library(current_user.id)
    active = [g for g in rows if not g["retired_at"]]
    retired = [g for g in rows if g["retired_at"]]
    return render_template(
        "library.html", active=active, retired=retired, **_form_context()
    )


@bp.post("/goals")
@login_required
def create():
    _, error = goals.create(
        current_user.id,
        title=request.form.get("title", ""),
        kind=request.form.get("kind", ""),
        category_id=request.form.get("category_id", type=int) or 0,
        subcategory_id=request.form.get("subcategory_id", type=int),
        default_target=request.form.get("default_target", type=int),
        recurring=bool(request.form.get("recurring")),
        notes=request.form.get("notes", ""),
    )
    if error:
        flash(error)
    return redirect(request.form.get("back") or url_for("library.index"))


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
