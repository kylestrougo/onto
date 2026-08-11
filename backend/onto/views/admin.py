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
    from .. import llm

    stats = {
        "users": query("SELECT COUNT(*) AS n FROM users", one=True)["n"],
        "goals": query("SELECT COUNT(*) AS n FROM goals", one=True)["n"],
        "commitments_week": query(
            "SELECT COUNT(*) AS n FROM commitments WHERE created_at > datetime('now', '-7 days')",
            one=True,
        )["n"],
        "events_by_tier": query(
            "SELECT tier, COUNT(*) AS n FROM events WHERE status = 'active'"
            " GROUP BY tier ORDER BY tier"
        ),
        "uncategorized": query(
            "SELECT COUNT(*) AS n FROM events WHERE status='active' AND category_id IS NULL",
            one=True,
        )["n"],
        "suggestions_week": query(
            "SELECT COUNT(*) AS n FROM suggestions WHERE created_at > datetime('now', '-7 days')",
            one=True,
        )["n"],
        "digests_week": query(
            "SELECT COUNT(*) AS n FROM digests WHERE created_at > datetime('now', '-7 days')",
            one=True,
        )["n"],
        "sick_sources": query(
            "SELECT name, consecutive_errors, quarantined_at FROM sources"
            " WHERE enabled = 0 OR quarantined_at IS NOT NULL OR consecutive_errors > 0"
        ),
        "models": llm.stats_rollup(7),
    }
    return render_template("admin/index.html", stats=stats)


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


# ── Event sources & corpus ───────────────────────────────────────────────


@bp.get("/sources")
@admin_required
def sources():
    rows = query("SELECT * FROM sources ORDER BY name")
    counts = {
        r["source_id"]: r["n"]
        for r in query(
            "SELECT source_id, COUNT(*) AS n FROM events WHERE status='active'"
            " GROUP BY source_id"
        )
    }
    return render_template("admin/sources.html", sources=rows, counts=counts)


@bp.post("/sources")
@admin_required
def add_source():
    name = (request.form.get("name") or "").strip()
    kind = request.form.get("kind") or ""
    try:
        tier = int(request.form.get("tier") or 0)
    except ValueError:
        tier = 0
    raw_config = request.form.get("config_json") or "{}"
    try:
        config = json.loads(raw_config)
        assert isinstance(config, dict)
    except (ValueError, AssertionError):
        flash("The config has to be a JSON object.")
        return redirect(url_for("admin.sources"))
    if not name or kind not in ("nyc_open_data", "ics", "jsonld", "llm_research"):
        flash("A source needs a name and a known kind.")
    elif tier not in (1, 2, 3):
        flash("Tier is 1 (official), 2 (venue feed) or 3 (LLM research).")
    elif kind != "llm_research" and not config.get("url"):
        flash("The config needs a url.")
    else:
        execute(
            "INSERT INTO sources (name, kind, tier, config_json) VALUES (?, ?, ?, ?)",
            (name, kind, tier, json.dumps(config)),
        )
        flash("Added. The next ingest run picks it up — or run `flask ingest` now.")
    return redirect(url_for("admin.sources"))


@bp.post("/sources/<int:source_id>")
@admin_required
def edit_source(source_id: int):
    action = request.form.get("action")
    if action == "disable":
        execute("UPDATE sources SET enabled = 0 WHERE id = ?", (source_id,))
    elif action == "enable":
        execute(
            "UPDATE sources SET enabled = 1, consecutive_errors = 0 WHERE id = ?",
            (source_id,),
        )
    elif action == "unquarantine":
        # Deliberate two-step: the events stay quarantined; only re-ingest
        # (or manual review) brings fresh ones in from this source.
        execute(
            "UPDATE sources SET quarantined_at = NULL, flag_count = 0, enabled = 1,"
            " consecutive_errors = 0 WHERE id = ?",
            (source_id,),
        )
    return redirect(url_for("admin.sources"))


@bp.get("/events")
@admin_required
def events():
    uncategorized = query(
        "SELECT e.*, s.name AS source_name FROM events e JOIN sources s ON s.id = e.source_id"
        " WHERE e.category_id IS NULL AND e.status = 'active' ORDER BY e.starts_at LIMIT 50"
    )
    recent = query(
        "SELECT e.*, s.name AS source_name, c.name AS category_name"
        " FROM events e JOIN sources s ON s.id = e.source_id"
        " LEFT JOIN categories c ON c.id = e.category_id"
        " WHERE e.status = 'active' AND e.category_id IS NOT NULL"
        " ORDER BY e.first_seen_at DESC LIMIT 50"
    )
    cats = query("SELECT * FROM categories WHERE retired = 0 ORDER BY position")
    return render_template(
        "admin/events.html", uncategorized=uncategorized, recent=recent, cats=cats
    )


@bp.post("/events/<int:event_id>/categorize")
@admin_required
def categorize_event(event_id: int):
    cat_id = request.form.get("category_id", type=int)
    if cat_id:
        execute(
            "UPDATE events SET category_id = ?, subcategory_id = NULL WHERE id = ?",
            (cat_id, event_id),
        )
    return redirect(url_for("admin.events"))


@bp.post("/events/<int:event_id>/remove")
@admin_required
def remove_event(event_id: int):
    execute("UPDATE events SET status = 'removed' WHERE id = ?", (event_id,))
    return redirect(url_for("admin.events"))
