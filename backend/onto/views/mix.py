"""The mix editor — "what balance do you want your weeks to have?"."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import disclosure, mix
from ..db import query

bp = Blueprint("mix", __name__)


@bp.get("/mix")
@login_required
def edit():
    if not disclosure.unlocked(current_user, "mix"):
        abort(404)  # not discovered yet — the page doesn't exist for them
    cats = query("SELECT * FROM categories WHERE retired = 0 ORDER BY position")
    return render_template(
        "mix.html",
        cats=cats,
        weekly=mix.targets(current_user.id, "week"),
        monthly=mix.targets(current_user.id, "month"),
    )


@bp.post("/mix")
@login_required
def save():
    if not disclosure.unlocked(current_user, "mix"):
        abort(404)
    period_kind = request.form.get("period_kind")
    if period_kind not in ("week", "month"):
        abort(400)
    percents: dict[int, int] = {}
    for key, raw in request.form.items():
        if key.startswith("cat-"):
            try:
                percents[int(key[4:])] = int(raw or 0)
            except ValueError:
                continue
    mix.set_targets(current_user.id, period_kind, percents)
    total = sum(p for p in percents.values() if p)
    if total > 100:
        flash("Those add up past 100% — that's allowed, just ambitious.")
    else:
        flash("Saved. The bar on your week shows how the plan compares.")
    return redirect(url_for("mix.edit"))
