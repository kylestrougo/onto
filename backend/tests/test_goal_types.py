"""The failure these tests prevent: goal types drifting from the spec table —
window goals landing in weeks, open goals committing without a number,
deadline goals without a date."""
from onto.db import query


def _gid(title):
    return query("SELECT id FROM goals WHERE title = ?", (title,), one=True)["id"]


def _make(client, app, title, kind, extra=None):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='hobby'", one=True)["id"]
    data = {"title": title, "kind": kind, "category_id": cat}
    data.update(extra or {})
    client.post("/goals", data=data)
    with app.app_context():
        return _gid(title)


def test_deadline_prompts_for_date_then_commits(app, signed_in):
    gid = _make(signed_in, app, "Ship the thing", "deadline")
    resp = signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    assert b"By when?" in resp.data
    signed_in.post(
        "/week/2026-W33/commitments", data={"goal_id": gid, "due_date": "2026-08-15"}
    )
    with app.app_context():
        row = query("SELECT due_date FROM commitments WHERE goal_id=?", (gid,), one=True)
        assert row["due_date"] == "2026-08-15"


def test_deadline_garbage_date_rejected(app, signed_in):
    gid = _make(signed_in, app, "Ship the thing", "deadline")
    resp = signed_in.post(
        "/week/2026-W33/commitments", data={"goal_id": gid, "due_date": "soonish"}
    )
    assert b"didn" in resp.data  # "That date didn't make sense."
    with app.app_context():
        assert query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True) is None


def test_window_goal_commits_to_month(app, signed_in):
    gid = _make(signed_in, app, "Museum sometime", "window")
    resp = signed_in.post("/month/2026-08/commitments", data={"goal_id": gid})
    assert resp.status_code == 200
    assert b"commitment-" in resp.data
    with app.app_context():
        row = query("SELECT period_kind, period_key FROM commitments WHERE goal_id=?", (gid,), one=True)
        assert (row["period_kind"], row["period_key"]) == ("month", "2026-08")


def test_open_goal_target_can_differ_per_period(app, signed_in):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='health'", one=True)["id"]
    signed_in.post("/goals", data={"title": "Run more", "kind": "open", "category_id": cat})
    with app.app_context():
        gid = _gid("Run more")
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid, "target": 3})
    signed_in.post("/week/2026-W34/commitments", data={"goal_id": gid, "target": 5})
    with app.app_context():
        targets = {
            r["period_key"]: r["target"]
            for r in query("SELECT period_key, target FROM commitments WHERE goal_id=?", (gid,))
        }
        assert targets == {"2026-W33": 3, "2026-W34": 5}


def test_month_view_renders(signed_in):
    resp = signed_in.get("/month/2026-08")
    assert resp.status_code == 200
    assert b"August 2026" in resp.data or b"This month" in resp.data


def test_countable_allowed_in_month_too(app, signed_in):
    with app.app_context():
        gid = _gid("Work out")
    resp = signed_in.post("/month/2026-08/commitments", data={"goal_id": gid})
    assert b"commitment-" in resp.data
