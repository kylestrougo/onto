"""The week view — the home screen. Everything else is a detour (spec 11.7)."""
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("week", __name__)


@bp.get("/")
@login_required
def home():
    # Phase 1 replaces this with a redirect to /week/<current key>.
    return render_template("week.html")
