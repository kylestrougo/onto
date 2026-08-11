"""The failure these tests prevent: suggestions ignoring the opt-in toggle,
resurfacing dismissed events, or drowning users past the weekly cap."""
import json

from onto.db import execute, query


def _uid():
    return query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]


def _mk_event(title, subcat_slug=None, cat_slug="culture", tier=2, days_out=3,
              borough="Manhattan", free=True):
    src = query("SELECT id FROM sources WHERE name='T'", one=True)
    if not src:
        execute("INSERT INTO sources (name, kind, tier, config_json) VALUES ('T','ics',2,'{}')")
        src = query("SELECT id FROM sources WHERE name='T'", one=True)
    cat = query("SELECT id FROM categories WHERE slug=?", (cat_slug,), one=True)["id"]
    sub = None
    if subcat_slug:
        sub = query(
            "SELECT id FROM subcategories WHERE slug=? AND category_id=?",
            (subcat_slug, cat), one=True,
        )["id"]
    return execute(
        "INSERT INTO events (source_id, external_id, dedupe_key, title, url, starts_at,"
        " borough, category_id, subcategory_id, is_free, tier)"
        " VALUES (?, ?, ?, ?, 'https://x/e', datetime('now', ?), ?, ?, ?, ?, ?)",
        (src["id"], title, title, title, f"+{days_out} days", borough, cat, sub,
         1 if free else 0, tier),
    )


def _enable_discovery(title):
    execute(
        "UPDATE goals SET discovery_enabled = 1 WHERE title = ?", (title,)
    )


def _run(app):
    from onto.discovery import matching

    user = query("SELECT * FROM users WHERE username='kyle'", one=True)
    return matching.run_for_user(user)


def test_no_discovery_goals_no_suggestions(app, signed_in):
    with app.app_context():
        _mk_event("Concert in the Park", "live-music")
        assert _run(app) == 0


def test_subcategory_match_beats_category_match(app, signed_in):
    with app.app_context():
        _enable_discovery("See some live music")  # Culture/Live music
        e_sub = _mk_event("Jazz Show", "live-music")
        e_cat = _mk_event("Random Culture Thing", None, "culture")
        _run(app)
        rows = query("SELECT event_id, score FROM suggestions ORDER BY score DESC")
        assert rows[0]["event_id"] == e_sub
        assert rows[0]["score"] > rows[1]["score"]


def test_mix_gap_boosts_score(app, signed_in):
    with app.app_context():
        from onto import mix, periods

        _enable_discovery("See some live music")
        culture = query("SELECT id FROM categories WHERE slug='culture'", one=True)["id"]
        e1 = _mk_event("Show A", "live-music")
        base_score = None
        _run(app)
        base_score = query("SELECT score FROM suggestions WHERE event_id=?", (e1,), one=True)["score"]
        # Wipe and set a culture gap; same event scores higher.
        execute("DELETE FROM suggestions")
        mix.set_targets(_uid(), "week", {culture: 50})
        _run(app)
        boosted = query("SELECT score FROM suggestions WHERE event_id=?", (e1,), one=True)["score"]
        assert boosted == base_score + 2.0


def test_dismissed_never_resurfaces(app, signed_in):
    with app.app_context():
        _enable_discovery("See some live music")
        e1 = _mk_event("Jazz Show", "live-music")
        _run(app)
        sid = query("SELECT id FROM suggestions WHERE event_id=?", (e1,), one=True)["id"]
    signed_in.post(f"/suggestions/{sid}/dismiss")
    with app.app_context():
        execute("DELETE FROM suggestions WHERE status IN ('pending','seen')")
        assert _run(app) == 0  # the dismissed event doesn't come back


def test_weekly_cap(app, signed_in):
    with app.app_context():
        _enable_discovery("See some live music")
        for i in range(8):
            _mk_event(f"Show {i}", "live-music", days_out=2 + i % 5)
        made = _run(app)
        assert made == 5  # MAX_PER_USER_PER_WEEK
        assert _run(app) == 0  # cap already spent this week


def test_borough_filter(app, signed_in):
    with app.app_context():
        _enable_discovery("See some live music")
        execute(
            "UPDATE users SET boroughs_json = ? WHERE username='kyle'",
            (json.dumps(["Brooklyn"]),),
        )
        _mk_event("Manhattan Show", "live-music", borough="Manhattan")
        e_bk = _mk_event("Brooklyn Show", "live-music", borough="Brooklyn")
        e_unknown = _mk_event("Somewhere Show", "live-music", borough="")
        _run(app)
        got = {r["event_id"] for r in query("SELECT event_id FROM suggestions")}
        # Brooklyn and unknown-borough events, never the Manhattan one.
        assert got == {e_bk, e_unknown}


def test_quarantined_and_dead_link_events_excluded(app, signed_in):
    with app.app_context():
        _enable_discovery("See some live music")
        e_dead = _mk_event("Dead Link Show", "live-music")
        execute("UPDATE events SET verify_ok = 0 WHERE id=?", (e_dead,))
        e_quar = _mk_event("Shady Show", "live-music")
        execute("UPDATE events SET status='quarantined' WHERE id=?", (e_quar,))
        assert _run(app) == 0
