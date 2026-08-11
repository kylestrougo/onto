"""The whole product in one pass, through real routes: signup → starter
library → drop a goal on the week → check off → friends → shared goal →
event suggestion accepted with source link → digest. The failure this test
prevents: any seam between the phases quietly coming apart."""
from unittest.mock import patch

from onto.db import execute, query
from tests.conftest import sign_up


def test_full_journey(app, client):
    # ── Kyle signs up: nothing starts empty ──────────────────────────────
    sign_up(client, username="kyle")
    home = client.get("/", follow_redirects=True)
    assert b"Work out" in home.data  # starter library in the side panel

    with app.app_context():
        from onto import periods

        week = periods.current_key("week", "America/New_York")
        workout = query(
            "SELECT g.id FROM goals g JOIN users u ON u.id=g.created_by"
            " WHERE u.username='kyle' AND g.title='Work out'", one=True,
        )["id"]
        novelty = query(
            "SELECT g.id FROM goals g JOIN users u ON u.id=g.created_by"
            " WHERE u.username='kyle' AND g.title='See some live music'", one=True,
        )["id"]

    # ── Drop goals onto the week and make progress ───────────────────────
    resp = client.post(f"/week/{week}/commitments", data={"goal_id": workout})
    assert b"commitment-" in resp.data
    client.post(f"/week/{week}/commitments", data={"goal_id": novelty})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (workout,), one=True)["id"]
    for _ in range(3):
        resp = client.post(f"/commitments/{cid}/log")
    assert b"is-done" in resp.data

    # ── Ana arrives; they become friends; the goal becomes shared ────────
    client.post("/logout")
    sign_up(client, username="ana")
    client.post("/friends/request", data={"username": "kyle"})
    client.post("/logout")
    client.post("/login", data={"username": "kyle", "password": "longenoughpw"})
    with app.app_context():
        fid = query("SELECT id FROM friendships", one=True)["id"]
    client.post(f"/friends/{fid}/accept")
    client.post(f"/goals/{novelty}/invite", data={"username": "ana"})
    client.post("/logout")
    client.post("/login", data={"username": "ana", "password": "longenoughpw"})
    with app.app_context():
        inv = query("SELECT id FROM goal_invites WHERE status='pending'", one=True)["id"]
    client.post(f"/invites/{inv}/accept")
    page = client.get("/friends")
    assert b"kyle" in page.data

    # ── Discovery: kyle opts the shared goal in; matching finds an event ─
    client.post("/logout")
    client.post("/login", data={"username": "kyle", "password": "longenoughpw"})
    client.post(f"/goals/{novelty}/discovery")
    with app.app_context():
        execute("INSERT INTO sources (name, kind, tier, config_json)"
                " VALUES ('NYC Parks','nyc_open_data',1,'{}')")
        src = query("SELECT id FROM sources WHERE name='NYC Parks'", one=True)["id"]
        cat = query("SELECT id FROM categories WHERE slug='culture'", one=True)["id"]
        sub = query(
            "SELECT id FROM subcategories WHERE slug='live-music' AND category_id=?",
            (cat,), one=True,
        )["id"]
        execute(
            "INSERT INTO events (source_id, external_id, dedupe_key, title, url,"
            " starts_at, borough, category_id, subcategory_id, is_free, tier)"
            " VALUES (?, 'p1', 'p1', 'Rooftop Jazz Night', 'https://parks/jazz',"
            " datetime('now', '+3 days'), 'Brooklyn', ?, ?, 1, 1)",
            (src, cat, sub),
        )
        from onto.discovery import matching

        kyle = query("SELECT * FROM users WHERE username='kyle'", one=True)
        assert matching.run_for_user(kyle) == 1
        # It's a shared goal, so the suggestion carries a group key.
        assert query("SELECT group_key FROM suggestions", one=True)["group_key"]

    page = client.get(f"/week/{week}")
    assert b"Rooftop Jazz Night" in page.data  # the week-view panel

    with app.app_context():
        sid = query("SELECT id FROM suggestions", one=True)["id"]
    client.post(f"/suggestions/{sid}/accept", data={"back": f"/week/{week}"})
    page = client.get(f"/week/{week}")
    # The novelty goal got its "what", link and all (specs 8.7 + Novelty).
    assert b"https://parks/jazz" in page.data

    # ── The weekly note arrives, facts from rows, prose validated ────────
    with app.app_context():
        from onto.notify import send

        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        send.ensure_prefs(uid)
        execute("UPDATE notification_prefs SET frequency='daily', send_hour=0"
                " WHERE user_id = ?", (uid,))

        def llm_prose(system, user, intent="generic", **kw):
            return {"opening": "Strong week already — the workout streak held.",
                    "goal_notes": [], "event_picks": [], "closing": "Go enjoy the jazz."}

        with patch("onto.notify.compose.check_url", return_value=True), \
             patch("onto.llm.generate", llm_prose):
            result = send.send_due_digests()
        assert result["sent"] == 1
    page = client.get("/digests")
    assert b"Work out" in page.data
    assert b"the workout streak held" in page.data

    # ── The recap tells the story ────────────────────────────────────────
    page = client.get(f"/recap/week/{week}")
    assert page.status_code == 200
    assert b"Work out" in page.data
