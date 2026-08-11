"""Admin screens. Complexity lives here, not in the user's face (spec 11.8).

Taxonomy (spec 3.2) and scoring weights (spec 5.1) are admin-only. New
categories and subcategories must arrive with event-discovery search terms
(spec 3.4) — the form enforces it.
"""
from __future__ import annotations

import json
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..auth import admin_required
from ..db import execute, query

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "x"


def _unique_slug(table: str, name: str, category_id: int | None = None) -> str:
    base = _slugify(name)
    slug = base
    n = 2
    while True:
        if table == "categories":
            row = query("SELECT 1 FROM categories WHERE slug = ?", (slug,), one=True)
        else:
            row = query(
                "SELECT 1 FROM subcategories WHERE slug = ? AND category_id = ?",
                (slug, category_id),
                one=True,
            )
        if not row:
            return slug
        slug = f"{base}-{n}"
        n += 1


def _terms(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


@bp.get("/")
@admin_required
def index():
    return render_template("admin/index.html")


# ── Taxonomy ─────────────────────────────────────────────────────────────


@bp.get("/taxonomy")
@admin_required
def taxonomy():
    cats = query("SELECT * FROM categories ORDER BY position")
    subs = query("SELECT * FROM subcategories ORDER BY name")
    by_cat: dict[int, list] = {}
    for s in subs:
        by_cat.setdefault(s["category_id"], []).append(s)
    return render_template(
        "admin/taxonomy.html",
        taxonomy=[(c, by_cat.get(c["id"], [])) for c in cats],
        loads=json.loads,
    )


@bp.post("/taxonomy/categories")
@admin_required
def add_category():
    name = (request.form.get("name") or "").strip()
    terms = _terms(request.form.get("search_terms", ""))
    example = (request.form.get("example") or "").strip()
    if not name:
        flash("The category needs a name.")
    elif not terms:
        # Spec 3.4: every category maps to discovery keywords, no exceptions.
        flash("Give it search terms — discovery needs them to find events.")
    else:
        pos = query("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM categories", one=True)["p"]
        execute(
            "INSERT INTO categories (name, slug, position, search_terms_json, example)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, _unique_slug("categories", name), pos, json.dumps(terms), example),
        )
    return redirect(url_for("admin.taxonomy"))


@bp.post("/taxonomy/categories/<int:cat_id>")
@admin_required
def edit_category(cat_id: int):
    action = request.form.get("action")
    if action == "retire":
        execute("UPDATE categories SET retired = 1 WHERE id = ?", (cat_id,))
    elif action == "unretire":
        execute("UPDATE categories SET retired = 0 WHERE id = ?", (cat_id,))
    elif action == "rename":
        name = (request.form.get("name") or "").strip()
        terms = _terms(request.form.get("search_terms", ""))
        if name and terms:
            execute(
                "UPDATE categories SET name = ?, search_terms_json = ? WHERE id = ?",
                (name, json.dumps(terms), cat_id),
            )
        else:
            flash("A category keeps its name and search terms — both are required.")
    return redirect(url_for("admin.taxonomy"))


@bp.post("/taxonomy/subcategories")
@admin_required
def add_subcategory():
    cat_id = request.form.get("category_id", type=int)
    name = (request.form.get("name") or "").strip()
    terms = _terms(request.form.get("search_terms", ""))
    if not (cat_id and name):
        flash("Pick a category and a name.")
    elif not terms:
        flash("Give it search terms — discovery needs them to find events.")
    else:
        execute(
            "INSERT INTO subcategories (category_id, name, slug, search_terms_json)"
            " VALUES (?, ?, ?, ?)",
            (cat_id, name, _unique_slug("subcategories", name, cat_id), json.dumps(terms)),
        )
    return redirect(url_for("admin.taxonomy"))


@bp.post("/taxonomy/subcategories/<int:sub_id>")
@admin_required
def edit_subcategory(sub_id: int):
    action = request.form.get("action")
    if action == "retire":
        execute("UPDATE subcategories SET retired = 1 WHERE id = ?", (sub_id,))
    elif action == "unretire":
        execute("UPDATE subcategories SET retired = 0 WHERE id = ?", (sub_id,))
    elif action == "rename":
        name = (request.form.get("name") or "").strip()
        terms = _terms(request.form.get("search_terms", ""))
        if name and terms:
            execute(
                "UPDATE subcategories SET name = ?, search_terms_json = ? WHERE id = ?",
                (name, json.dumps(terms), sub_id),
            )
        else:
            flash("A subcategory keeps its name and search terms — both are required.")
    return redirect(url_for("admin.taxonomy"))


# ── Scoring weights ──────────────────────────────────────────────────────


@bp.get("/weights")
@admin_required
def weights():
    cats = query("SELECT * FROM categories ORDER BY position")
    subs = query("SELECT * FROM subcategories ORDER BY name")
    by_cat: dict[int, list] = {}
    for s in subs:
        by_cat.setdefault(s["category_id"], []).append(s)
    return render_template(
        "admin/weights.html",
        taxonomy=[(c, by_cat.get(c["id"], [])) for c in cats],
    )


@bp.post("/weights")
@admin_required
def save_weights():
    """Points per category/subcategory. Blank subcategory = inherit."""
    for key, raw in request.form.items():
        raw = raw.strip()
        if key.startswith("cat-"):
            try:
                pts = max(0.0, min(float(raw), 100.0))
            except ValueError:
                continue
            execute("UPDATE categories SET points = ? WHERE id = ?", (pts, int(key[4:])))
        elif key.startswith("sub-"):
            if raw == "":
                execute("UPDATE subcategories SET points = NULL WHERE id = ?", (int(key[4:]),))
            else:
                try:
                    pts = max(0.0, min(float(raw), 100.0))
                except ValueError:
                    continue
                execute("UPDATE subcategories SET points = ? WHERE id = ?", (pts, int(key[4:])))
    flash("Weights saved. They apply to every user's score from now on.")
    return redirect(url_for("admin.weights"))
