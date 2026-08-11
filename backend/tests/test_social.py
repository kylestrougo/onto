"""The failure these tests prevent: the friendship pair-uniqueness being
dodged by swapping directions, or strangers sharing goals."""
from onto.db import query
from tests.conftest import sign_up


def _uid(name):
    return query("SELECT id FROM users WHERE username=?", (name,), one=True)["id"]


def test_request_and_accept(app, client):
    sign_up(client, username="alice")
    client.post("/logout")
    sign_up(client, username="bob")
    client.post("/friends/request", data={"username": "alice"})
    client.post("/logout")
    client.post("/login", data={"username": "alice", "password": "longenoughpw"})
    with app.app_context():
        fid = query("SELECT id FROM friendships WHERE status='pending'", one=True)["id"]
    client.post(f"/friends/{fid}/accept")
    with app.app_context():
        from onto import social

        assert social.are_friends(_uid("alice"), _uid("bob"))


def test_reverse_request_counts_as_accept(app, client):
    sign_up(client, username="alice")
    client.post("/friends/request", data={"username": "bob"})  # bob doesn't exist yet
    client.post("/logout")
    sign_up(client, username="bob")
    client.post("/logout")
    client.post("/login", data={"username": "alice", "password": "longenoughpw"})
    client.post("/friends/request", data={"username": "bob"})
    client.post("/logout")
    client.post("/login", data={"username": "bob", "password": "longenoughpw"})
    # Bob asks alice back — that's an accept, not a duplicate row.
    client.post("/friends/request", data={"username": "alice"})
    with app.app_context():
        from onto import social

        assert social.are_friends(_uid("alice"), _uid("bob"))
        n = query("SELECT COUNT(*) AS n FROM friendships", one=True)["n"]
        assert n == 1


def test_pair_unique_both_directions(app, client):
    sign_up(client, username="alice")
    client.post("/logout")
    sign_up(client, username="bob")
    client.post("/friends/request", data={"username": "alice"})
    client.post("/friends/request", data={"username": "alice"})  # again
    with app.app_context():
        assert query("SELECT COUNT(*) AS n FROM friendships", one=True)["n"] == 1


def test_stranger_cannot_be_invited_to_goal(app, client):
    sign_up(client, username="alice")
    with app.app_context():
        gid = query(
            "SELECT g.id FROM goals g JOIN users u ON u.id=g.created_by"
            " WHERE u.username='alice' AND g.title='Cook something new'",
            one=True,
        )["id"]
    client.post("/logout")
    sign_up(client, username="mallory")
    client.post("/logout")
    client.post("/login", data={"username": "alice", "password": "longenoughpw"})
    client.post(f"/goals/{gid}/invite", data={"username": "mallory"})
    with app.app_context():
        # Not friends → no invite row.
        assert query("SELECT id FROM goal_invites", one=True) is None
