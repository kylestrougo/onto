"""The failure these tests prevent: shared goals splitting into two separate
copies, one member's logs not counting, or members not both being scored."""
from onto.db import query
from tests.conftest import sign_up


def _uid(name):
    return query("SELECT id FROM users WHERE username=?", (name,), one=True)["id"]


def _make_shared_goal(app, client):
    """alice + bob become friends; alice shares 'Work out' (countable 3);
    bob accepts. Returns the goal id, with bob signed in."""
    sign_up(client, username="alice")
    client.post("/logout")
    sign_up(client, username="bob")
    client.post("/friends/request", data={"username": "alice"})
    client.post("/logout")
    client.post("/login", data={"username": "alice", "password": "longenoughpw"})
    with app.app_context():
        fid = query("SELECT id FROM friendships", one=True)["id"]
    client.post(f"/friends/{fid}/accept")
    with app.app_context():
        gid = query(
            "SELECT g.id FROM goals g JOIN users u ON u.id=g.created_by"
            " WHERE u.username='alice' AND g.title='Work out'",
            one=True,
        )["id"]
    client.post(f"/goals/{gid}/invite", data={"username": "bob"})
    client.post("/logout")
    client.post("/login", data={"username": "bob", "password": "longenoughpw"})
    with app.app_context():
        inv = query("SELECT id FROM goal_invites WHERE status='pending'", one=True)["id"]
    client.post(f"/invites/{inv}/accept")
    return gid


def test_invite_accept_creates_membership(app, client):
    gid = _make_shared_goal(app, client)
    with app.app_context():
        members = query(
            "SELECT u.username, gm.role FROM goal_members gm JOIN users u ON u.id=gm.user_id"
            " WHERE gm.goal_id=? ORDER BY u.username",
            (gid,),
        )
        assert [(m["username"], m["role"]) for m in members] == [
            ("alice", "owner"), ("bob", "member"),
        ]


def test_shared_goal_appears_in_both_libraries_and_weeks(app, client):
    gid = _make_shared_goal(app, client)
    # bob (signed in) commits the shared goal to the week.
    client.post("/week/2026-W33/commitments", data={"goal_id": gid})
    page = client.get("/week/2026-W33")
    assert b"Work out" in page.data
    assert b"together" in page.data
    # alice sees the same single commitment.
    client.post("/logout")
    client.post("/login", data={"username": "alice", "password": "longenoughpw"})
    page = client.get("/week/2026-W33")
    assert b"Work out" in page.data
    with app.app_context():
        n = query("SELECT COUNT(*) AS n FROM commitments WHERE goal_id=?", (gid,), one=True)["n"]
        assert n == 1  # one shared commitment, not one per member


def test_both_members_logs_sum_and_both_score(app, client):
    gid = _make_shared_goal(app, client)
    client.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    # bob logs 2
    client.post(f"/commitments/{cid}/log")
    client.post(f"/commitments/{cid}/log")
    # alice logs the third
    client.post("/logout")
    client.post("/login", data={"username": "alice", "password": "longenoughpw"})
    resp = client.post(f"/commitments/{cid}/log")
    assert b"is-done" in resp.data
    assert b"alice" in resp.data and b"bob" in resp.data  # attribution
    with app.app_context():
        from onto import scoring

        # Full points to every member (design decision, docs/decisions.md).
        for name in ("alice", "bob"):
            assert scoring.period_score(_uid(name), "week", "2026-W33")["earned"] == 1.0


def test_member_but_not_owner_can_log_but_goal_stays_one(app, client):
    gid = _make_shared_goal(app, client)
    with app.app_context():
        n = query("SELECT COUNT(*) AS n FROM goals WHERE title='Work out'", one=True)["n"]
        # alice's copy + bob's own starter copy exist; sharing added none.
        assert n == 2
