"""The failure these tests prevent: goals losing history when retired
(spec 1.7), or one user touching another's library."""
from onto.db import query
from tests.conftest import sign_up


def _goal_id(client, title):
    row = query("SELECT id FROM goals WHERE title = ?", (title,), one=True)
    return row["id"]


def test_create_goal(app, signed_in):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='hobby'", one=True)["id"]
    resp = signed_in.post(
        "/goals",
        data={"title": "Go climbing", "kind": "countable", "category_id": cat,
              "default_target": 2, "recurring": "1"},
    )
    assert resp.status_code == 302
    with app.app_context():
        row = query("SELECT * FROM goals WHERE title='Go climbing'", one=True)
        assert row["kind"] == "countable"
        assert row["default_target"] == 2
        assert row["recurring"] == 1


def test_countable_requires_target(app, signed_in):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='hobby'", one=True)["id"]
    signed_in.post("/goals", data={"title": "Vague thing", "kind": "countable", "category_id": cat})
    with app.app_context():
        assert query("SELECT id FROM goals WHERE title='Vague thing'", one=True) is None


def test_retire_keeps_history(app, signed_in):
    with app.app_context():
        gid = _goal_id(signed_in, "Cook something new")
    # Commit it to a week and complete it, then retire.
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id = ?", (gid,), one=True)["id"]
    signed_in.post(f"/commitments/{cid}/log")
    signed_in.post(f"/goals/{gid}/retire")
    with app.app_context():
        goal = query("SELECT * FROM goals WHERE id = ?", (gid,), one=True)
        assert goal["retired_at"] is not None
        # History intact (spec 1.6/1.7).
        assert query("SELECT COUNT(*) AS n FROM commitments WHERE goal_id=?", (gid,), one=True)["n"] == 1
        assert query(
            "SELECT COUNT(*) AS n FROM completions cp JOIN commitments cm ON cm.id=cp.commitment_id"
            " WHERE cm.goal_id=?",
            (gid,),
            one=True,
        )["n"] == 1
    # Library page shows it under retired with its record.
    page = signed_in.get("/library")
    assert b"Tucked away" in page.data


def test_cannot_touch_another_users_goal(app, client):
    sign_up(client, username="alice")
    with app.app_context():
        gid = _goal_id(client, "Cook something new")  # alice's copy
    client.post("/logout")
    sign_up(client, username="bob")
    # bob tries to retire alice's goal, edit it, and commit it.
    client.post(f"/goals/{gid}/retire")
    with app.app_context():
        assert query("SELECT retired_at FROM goals WHERE id=?", (gid,), one=True)["retired_at"] is None
    assert client.get(f"/goals/{gid}/edit").status_code == 404
    resp = client.post("/week/2026-W33/commitments", data={"goal_id": gid})
    assert resp.status_code == 404
