"""The failure these tests prevent: accepting a suggestion not creating the
linked commitment (spec 8.7), novelty goals never getting their 'what', or
flags not tripping quarantine (spec 10.6)."""
from onto.db import execute, query
from tests.conftest import sign_up


def _uid(name="kyle"):
    return query("SELECT id FROM users WHERE username=?", (name,), one=True)["id"]


def _seed_suggestion(app, goal_title="See some live music", username="kyle"):
    """A matched suggestion for the user's goal, via a real corpus event."""
    execute("INSERT OR IGNORE INTO sources (name, kind, tier, config_json)"
            " VALUES ('T','ics',2,'{}')")
    src = query("SELECT id FROM sources WHERE name='T'", one=True)["id"]
    cat = query("SELECT id FROM categories WHERE slug='culture'", one=True)["id"]
    eid = execute(
        "INSERT INTO events (source_id, external_id, dedupe_key, title, url,"
        " starts_at, borough, category_id, is_free, tier)"
        " VALUES (?, 'e1', 'e1', 'Rooftop Jazz', 'https://venue/x',"
        " datetime('now', '+2 days'), 'Brooklyn', ?, 1, 2)",
        (src, cat),
    )
    gid = query(
        "SELECT g.id FROM goals g JOIN users u ON u.id = g.created_by"
        " WHERE u.username=? AND g.title=?",
        (username, goal_title), one=True,
    )["id"]
    sid = execute(
        "INSERT INTO suggestions (user_id, event_id, goal_id, score, reason)"
        " VALUES (?, ?, ?, 5.0, 'matches')",
        (_uid(username), eid, gid),
    )
    return sid, eid, gid


def test_accept_creates_commitment_with_source_link(app, signed_in):
    with app.app_context():
        sid, eid, gid = _seed_suggestion(app)
    resp = signed_in.post(f"/suggestions/{sid}/accept")
    assert resp.status_code == 302
    with app.app_context():
        c = query("SELECT * FROM commitments WHERE goal_id=?", (gid,), one=True)
        assert c is not None
        assert c["event_id"] == eid and c["suggestion_id"] == sid
        s = query("SELECT status, commitment_id FROM suggestions WHERE id=?", (sid,), one=True)
        assert s["status"] == "accepted" and s["commitment_id"] == c["id"]
    # The week card links out to the event.
    page = signed_in.get("/")
    page = signed_in.get(page.headers["Location"])
    assert b"Rooftop Jazz" in page.data
    assert b"https://venue/x" in page.data


def test_accept_binds_event_to_existing_novelty_commitment(app, signed_in):
    """The novelty flow: goal already on the week, discovery supplies the
    'what'."""
    with app.app_context():
        sid, eid, gid = _seed_suggestion(app)
        from onto import periods

        week = periods.current_key("week", "America/New_York")
    signed_in.post(f"/week/{week}/commitments", data={"goal_id": gid})
    with app.app_context():
        before = query("SELECT COUNT(*) AS n FROM commitments", one=True)["n"]
    signed_in.post(f"/suggestions/{sid}/accept")
    with app.app_context():
        after = query("SELECT COUNT(*) AS n FROM commitments", one=True)["n"]
        assert after == before  # bound, not duplicated
        c = query("SELECT event_id FROM commitments WHERE goal_id=?", (gid,), one=True)
        assert c["event_id"] == eid


def test_flag_quarantines_event_and_counts_source(app, signed_in):
    with app.app_context():
        sid, eid, _ = _seed_suggestion(app)
    signed_in.post(f"/suggestions/{sid}/flag", data={"reason": "dead link"})
    with app.app_context():
        assert query("SELECT status FROM events WHERE id=?", (eid,), one=True)["status"] == "quarantined"
        src = query("SELECT flag_count, quarantined_at FROM sources WHERE name='T'", one=True)
        assert src["flag_count"] == 1
        assert src["quarantined_at"] is None  # one user isn't a pattern


def test_three_distinct_users_quarantine_the_source(app, client):
    with app.app_context():
        pass
    for i, name in enumerate(("kyle", "ana", "ben")):
        sign_up(client, username=name)
        with app.app_context():
            # Each user flags a different event from the same source.
            execute("INSERT OR IGNORE INTO sources (name, kind, tier, config_json)"
                    " VALUES ('T','ics',2,'{}')")
            src = query("SELECT id FROM sources WHERE name='T'", one=True)["id"]
            cat = query("SELECT id FROM categories WHERE slug='culture'", one=True)["id"]
            eid = execute(
                "INSERT INTO events (source_id, external_id, dedupe_key, title, url,"
                f" starts_at, category_id, tier) VALUES (?, 'e{i}', 'e{i}', 'Ev {i}',"
                " 'https://x', datetime('now', '+2 days'), ?, 2)",
                (src, cat),
            )
            sid = execute(
                "INSERT INTO suggestions (user_id, event_id, score) VALUES (?, ?, 1.0)",
                (_uid(name), eid),
            )
        client.post(f"/suggestions/{sid}/flag")
        client.post("/logout")
    with app.app_context():
        src = query("SELECT quarantined_at, enabled FROM sources WHERE name='T'", one=True)
        assert src["quarantined_at"] is not None
        assert src["enabled"] == 0
        # Every active event from the source went down with it.
        n_active = query(
            "SELECT COUNT(*) AS n FROM events WHERE status='active'", one=True
        )["n"]
        assert n_active == 0


def test_discovery_toggle_defaults_off_and_flips(app, signed_in):
    with app.app_context():
        gid = query("SELECT id FROM goals WHERE title='Go for a run'", one=True)["id"]
        assert query("SELECT discovery_enabled FROM goals WHERE id=?", (gid,), one=True)[
            "discovery_enabled"
        ] == 0  # spec 11.6: default off
    signed_in.post(f"/goals/{gid}/discovery")
    with app.app_context():
        assert query("SELECT discovery_enabled FROM goals WHERE id=?", (gid,), one=True)[
            "discovery_enabled"
        ] == 1


def test_discover_page_states(app, signed_in):
    page = signed_in.get("/discover")
    assert b"flip the toggle" in page.data  # no discovery goals yet
    with app.app_context():
        sid, _, _ = _seed_suggestion(app)
    page = signed_in.get("/discover")
    assert b"Rooftop Jazz" in page.data
    with app.app_context():
        assert query("SELECT status FROM suggestions WHERE id=?", (sid,), one=True)[
            "status"
        ] == "seen"
