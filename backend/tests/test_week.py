"""The failure these tests prevent: the core loop breaking — drop a goal
into the week, check it off, see progress — or double-adds corrupting it."""
from onto.db import query


def _gid(title):
    return query("SELECT id FROM goals WHERE title = ?", (title,), one=True)["id"]


def test_home_redirects_to_current_week(signed_in):
    resp = signed_in.get("/")
    assert resp.status_code == 302
    assert "/week/" in resp.headers["Location"]


def test_bad_week_key_404s(signed_in):
    assert signed_in.get("/week/2026-W99").status_code == 404
    assert signed_in.get("/week/garbage").status_code == 404


def test_add_binary_goal_returns_card(app, signed_in):
    with app.app_context():
        gid = _gid("Cook something new")
    resp = signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    assert resp.status_code == 200
    assert b"commitment-" in resp.data
    assert b"Done" in resp.data
    # OOB fragments ride along: progress badge + chip removal.
    assert b'hx-swap-oob="true"' in resp.data
    assert b'hx-swap-oob="delete"' in resp.data


def test_add_twice_is_idempotent(app, signed_in):
    with app.app_context():
        gid = _gid("Cook something new")
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        n = query("SELECT COUNT(*) AS n FROM commitments WHERE goal_id=?", (gid,), one=True)["n"]
        assert n == 1


def test_same_goal_in_two_weeks(app, signed_in):
    with app.app_context():
        gid = _gid("Cook something new")
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    signed_in.post("/week/2026-W34/commitments", data={"goal_id": gid})
    with app.app_context():
        n = query("SELECT COUNT(*) AS n FROM commitments WHERE goal_id=?", (gid,), one=True)["n"]
        assert n == 2


def test_countable_partial_logging(app, signed_in):
    with app.app_context():
        gid = _gid("Work out")  # starter, target 3
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]

    signed_in.post(f"/commitments/{cid}/log")
    resp = signed_in.post(f"/commitments/{cid}/log")
    assert b"2/3" in resp.data
    with app.app_context():
        row = query("SELECT completed_at FROM commitments WHERE id=?", (cid,), one=True)
        assert row["completed_at"] is None  # 2 of 3 is not done

    resp = signed_in.post(f"/commitments/{cid}/log")
    assert b"is-done" in resp.data
    with app.app_context():
        assert query("SELECT completed_at FROM commitments WHERE id=?", (cid,), one=True)["completed_at"]

    # Extra logs past the target are ignored.
    signed_in.post(f"/commitments/{cid}/log")
    with app.app_context():
        n = query("SELECT COUNT(*) AS n FROM completions WHERE commitment_id=?", (cid,), one=True)["n"]
        assert n == 3

    # Undo drops it back below done.
    signed_in.post(f"/commitments/{cid}/unlog")
    with app.app_context():
        assert query("SELECT completed_at FROM commitments WHERE id=?", (cid,), one=True)["completed_at"] is None


def test_open_goal_prompts_for_target(app, signed_in):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='health'", one=True)["id"]
    signed_in.post("/goals", data={"title": "Run more", "kind": "open", "category_id": cat})
    with app.app_context():
        gid = _gid("Run more")
    # Drop without a target → inline prompt, no commitment yet (spec: Open).
    resp = signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    assert b"How many times?" in resp.data
    with app.app_context():
        assert query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True) is None
    # Re-post with the target → real commitment.
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid, "target": 4})
    with app.app_context():
        row = query("SELECT target FROM commitments WHERE goal_id=?", (gid,), one=True)
        assert row["target"] == 4


def test_window_goal_rejected_from_week(app, signed_in):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='culture'", one=True)["id"]
    signed_in.post("/goals", data={"title": "Museum month", "kind": "window", "category_id": cat})
    with app.app_context():
        gid = _gid("Museum month")
    resp = signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    assert b"month" in resp.data
    with app.app_context():
        assert query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True) is None


def test_remove_restores_chip_and_badge(app, signed_in):
    with app.app_context():
        gid = _gid("Cook something new")
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    resp = signed_in.delete(f"/commitments/{cid}")
    assert resp.status_code == 200
    assert b"goal-chip-" in resp.data  # chip comes back OOB
    with app.app_context():
        assert query("SELECT id FROM commitments WHERE id=?", (cid,), one=True) is None


def test_week_page_renders_committed_and_available(app, signed_in):
    with app.app_context():
        gid = _gid("Cook something new")
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    page = signed_in.get("/week/2026-W33")
    assert page.status_code == 200
    assert b"Cook something new" in page.data
    assert b"Work out" in page.data  # still in the side panel
    assert page.data.count(b"Cook something new") == 1  # not offered twice
