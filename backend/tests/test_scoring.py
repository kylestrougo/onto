"""The failure these tests prevent: scores that lie — partial credit
miscounted, late deadlines paid, or mix/weights leaking into each other."""
import pytest

from onto.db import execute, query


def _gid(title):
    return query("SELECT id FROM goals WHERE title = ?", (title,), one=True)["id"]


def _uid():
    return query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]


def test_partial_credit_two_of_three(app, signed_in):
    with app.app_context():
        gid = _gid("Work out")  # countable, target 3
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    signed_in.post(f"/commitments/{cid}/log")
    signed_in.post(f"/commitments/{cid}/log")
    with app.app_context():
        from onto import scoring

        score = scoring.period_score(_uid(), "week", "2026-W33")
        # One flat-weight goal at 2/3 → 0.67 of 1 point.
        assert score["possible"] == 1.0
        assert score["earned"] == pytest.approx(0.67, abs=0.01)
        assert score["percent"] == 67


def test_deadline_late_scores_zero_but_counts_in_history(app, signed_in):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='upskilling'", one=True)["id"]
    signed_in.post("/goals", data={"title": "Finish course", "kind": "deadline", "category_id": cat})
    with app.app_context():
        gid = _gid("Finish course")
    signed_in.post(
        "/week/2026-W33/commitments", data={"goal_id": gid, "due_date": "2026-08-11"}
    )
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    signed_in.post(f"/commitments/{cid}/log")
    with app.app_context():
        # Force the completion to look late (after the due date).
        execute("UPDATE commitments SET completed_at = '2026-08-14 10:00:00' WHERE id = ?", (cid,))
        from onto import scoring

        score = scoring.period_score(_uid(), "week", "2026-W33")
        assert score["earned"] == 0.0  # late = no points
        # ...but the goal's history still shows a completion (spec: history).
        n = query(
            "SELECT COUNT(*) AS n FROM commitments WHERE goal_id=? AND completed_at IS NOT NULL",
            (gid,), one=True,
        )["n"]
        assert n == 1


def test_deadline_on_time_scores_full(app, signed_in):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='upskilling'", one=True)["id"]
    signed_in.post("/goals", data={"title": "Finish course", "kind": "deadline", "category_id": cat})
    with app.app_context():
        gid = _gid("Finish course")
    signed_in.post(
        "/week/2026-W33/commitments", data={"goal_id": gid, "due_date": "2099-01-01"}
    )
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    signed_in.post(f"/commitments/{cid}/log")
    with app.app_context():
        from onto import scoring

        assert scoring.period_score(_uid(), "week", "2026-W33")["percent"] == 100


def test_subcategory_weight_overrides_category(app, signed_in):
    with app.app_context():
        gid = _gid("Go for a run")  # Health/Cardio, countable target 2
        execute("UPDATE categories SET points = 2.0 WHERE slug = 'health'")
        execute(
            "UPDATE subcategories SET points = 5.0 WHERE slug = 'cardio'"
            " AND category_id = (SELECT id FROM categories WHERE slug='health')"
        )
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    signed_in.post(f"/commitments/{cid}/log")
    signed_in.post(f"/commitments/{cid}/log")
    with app.app_context():
        from onto import scoring

        score = scoring.period_score(_uid(), "week", "2026-W33")
        assert score["possible"] == 5.0  # subcategory 5.0 beats category 2.0
        assert score["earned"] == 5.0


def test_scores_comparable_between_users(app, client):
    """Same actions, same weights → same score (spec 5.6)."""
    from tests.conftest import sign_up

    results = {}
    for name in ("alice", "bob"):
        sign_up(client, username=name)
        with app.app_context():
            gid = query(
                "SELECT g.id FROM goals g JOIN users u ON u.id=g.created_by"
                " WHERE u.username=? AND g.title='Cook something new'",
                (name,), one=True,
            )["id"]
        client.post("/week/2026-W33/commitments", data={"goal_id": gid})
        with app.app_context():
            cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
        client.post(f"/commitments/{cid}/log")
        with app.app_context():
            from onto import scoring

            uid = query("SELECT id FROM users WHERE username=?", (name,), one=True)["id"]
            results[name] = scoring.period_score(uid, "week", "2026-W33")
        client.post("/logout")
    assert results["alice"]["earned"] == results["bob"]["earned"] == 1.0
