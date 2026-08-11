"""The failure these tests prevent: private goals leaking into feeds or
leaderboards (spec 7.2, 5.5)."""
from onto.db import execute, query
from tests.conftest import sign_up


def _uid(name):
    return query("SELECT id FROM users WHERE username=?", (name,), one=True)["id"]


def _befriend_alice_bob(app, client):
    sign_up(client, username="alice")
    client.post("/logout")
    sign_up(client, username="bob")
    client.post("/friends/request", data={"username": "alice"})
    client.post("/logout")
    client.post("/login", data={"username": "alice", "password": "longenoughpw"})
    with app.app_context():
        fid = query("SELECT id FROM friendships", one=True)["id"]
    client.post(f"/friends/{fid}/accept")
    # alice stays signed in


def _alices_goal(title="Cook something new"):
    return query(
        "SELECT g.id FROM goals g JOIN users u ON u.id=g.created_by"
        " WHERE u.username='alice' AND g.title=?",
        (title,),
        one=True,
    )["id"]


def test_private_goal_never_reaches_a_friends_feed(app, client):
    _befriend_alice_bob(app, client)
    with app.app_context():
        gid = _alices_goal()
    client.post("/week/2026-W33/commitments", data={"goal_id": gid})  # private by default
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    client.post(f"/commitments/{cid}/log")
    client.post("/logout")
    client.post("/login", data={"username": "bob", "password": "longenoughpw"})
    with app.app_context():
        from onto import feed

        assert feed.feed_for(_uid("bob")) == []


def test_friends_goal_shows_in_feed(app, client):
    _befriend_alice_bob(app, client)
    with app.app_context():
        gid = _alices_goal()
        execute("UPDATE goals SET visibility='friends' WHERE id=?", (gid,))
    client.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    client.post(f"/commitments/{cid}/log")
    client.post("/logout")
    client.post("/login", data={"username": "bob", "password": "longenoughpw"})
    with app.app_context():
        from onto import feed

        items = feed.feed_for(_uid("bob"))
        assert {i["verb"] for i in items} == {"committed", "completed"}
        assert all(i["goal_title"] == "Cook something new" for i in items)
    page = client.get("/feed")
    assert b"Cook something new" in page.data


def test_making_goal_private_erases_history_from_feed(app, client):
    _befriend_alice_bob(app, client)
    with app.app_context():
        gid = _alices_goal()
        execute("UPDATE goals SET visibility='friends' WHERE id=?", (gid,))
    client.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        execute("UPDATE goals SET visibility='private' WHERE id=?", (gid,))
        from onto import feed

        assert feed.feed_for(_uid("bob")) == []


def test_leaderboard_hides_score_without_visible_goals(app, client):
    _befriend_alice_bob(app, client)
    with app.app_context():
        from onto import feed, periods

        week = periods.current_key("week", "America/New_York")
        board = feed.leaderboard(_uid("bob"), week)
        alice_row = next(r for r in board if r["username"] == "alice")
        assert alice_row["visible"] is False and alice_row["earned"] is None
        # alice opens one goal to friends → her score appears.
        execute("UPDATE goals SET visibility='friends' WHERE id=?", (_alices_goal(),))
        board = feed.leaderboard(_uid("bob"), week)
        alice_row = next(r for r in board if r["username"] == "alice")
        assert alice_row["visible"] is True and alice_row["earned"] is not None


def test_strangers_see_nothing(app, client):
    sign_up(client, username="alice")
    with app.app_context():
        gid = _alices_goal()
        execute("UPDATE goals SET visibility='friends' WHERE id=?", (gid,))
    client.post("/week/2026-W33/commitments", data={"goal_id": gid})
    client.post("/logout")
    sign_up(client, username="stranger")
    with app.app_context():
        from onto import feed

        assert feed.feed_for(_uid("stranger")) == []
        assert feed.visible_goals(_uid("stranger"), _uid("alice")) == []
    page = client.get("/people/alice")
    assert b"Nothing shared with you" in page.data


def test_public_goal_visible_to_any_user(app, client):
    sign_up(client, username="alice")
    with app.app_context():
        gid = _alices_goal()
        execute("UPDATE goals SET visibility='public' WHERE id=?", (gid,))
    client.post("/logout")
    sign_up(client, username="stranger")
    page = client.get("/people/alice")
    assert b"Cook something new" in page.data


def test_photo_upload_and_rejection(app, client):
    import io

    sign_up(client, username="alice")
    with app.app_context():
        gid = _alices_goal()
    client.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    client.post(f"/commitments/{cid}/log")

    # Wrong type is dropped silently.
    client.post(
        f"/commitments/{cid}/photo",
        data={"photo": (io.BytesIO(b"#!/bin/sh"), "evil.sh")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert query(
            "SELECT photo_path FROM completions WHERE commitment_id=?", (cid,), one=True
        )["photo_path"] is None

    # A real image lands and shows on the card.
    resp = client.post(
        f"/commitments/{cid}/photo",
        data={"photo": (io.BytesIO(b"\x89PNG fake image bytes"), "proof.png")},
        content_type="multipart/form-data",
    )
    assert b"photo-thumb" in resp.data
    with app.app_context():
        path = query(
            "SELECT photo_path FROM completions WHERE commitment_id=?", (cid,), one=True
        )["photo_path"]
        assert path and path.endswith(".png")
