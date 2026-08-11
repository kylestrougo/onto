"""The failure these tests prevent: the mix leaking into scoring (spec 4.8
says it never does), gaps ordering wrongly for discovery, or the live bar
falling out of step with the plan."""
import pytest

from onto.db import execute, query


def _uid():
    return query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]


def _cat(slug):
    return query("SELECT id FROM categories WHERE slug=?", (slug,), one=True)["id"]


def _gid(title):
    return query("SELECT id FROM goals WHERE title = ?", (title,), one=True)["id"]


def _unlock_mix(app):
    with app.app_context():
        from onto import disclosure

        disclosure.unlock(_uid(), "mix")


def test_mix_editor_hidden_until_unlocked(app, signed_in):
    assert signed_in.get("/mix").status_code == 404
    _unlock_mix(app)
    assert signed_in.get("/mix").status_code == 200


def test_save_targets_neednt_total_100(app, signed_in):
    _unlock_mix(app)
    with app.app_context():
        health, social = _cat("health"), _cat("social")
    resp = signed_in.post(
        "/mix",
        data={"period_kind": "week", f"cat-{health}": "30", f"cat-{social}": "20"},
    )
    assert resp.status_code == 302
    with app.app_context():
        from onto import mix

        assert mix.targets(_uid(), "week") == {health: 30, social: 20}


def test_planned_and_gaps(app, signed_in):
    _unlock_mix(app)
    with app.app_context():
        health, social, culture = _cat("health"), _cat("social"), _cat("culture")
        from onto import mix

        mix.set_targets(_uid(), "week", {health: 40, social: 30, culture: 10})
    # Plan two health goals and one social — culture is left empty.
    for title in ("Work out", "Go for a run", "Call a friend or family member"):
        with app.app_context():
            gid = _gid(title)
        signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        from onto import mix

        planned = mix.planned_mix(_uid(), "week", "2026-W33")
        assert planned[health] == pytest.approx(66.7, abs=0.1)
        assert planned[social] == pytest.approx(33.3, abs=0.1)
        gaps = mix.gaps(_uid(), "week", "2026-W33")
        # Culture (10 - 0) beats social (30 - 33 → negative, dropped);
        # health is over target too. Only culture is under-filled.
        assert [g[0] for g in gaps] == [culture]

        # An empty week: every target is a gap, biggest first.
        gaps_empty = mix.gaps(_uid(), "week", "2026-W40")
        assert [g[0] for g in gaps_empty] == [health, social, culture]


def test_mix_never_touches_score(app, signed_in):
    """Regression pin for spec 4.8."""
    with app.app_context():
        gid = _gid("Cook something new")
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (gid,), one=True)["id"]
    signed_in.post(f"/commitments/{cid}/log")
    with app.app_context():
        from onto import mix, scoring

        before = scoring.period_score(_uid(), "week", "2026-W33")
        # A wildly lopsided mix target appears...
        mix.set_targets(_uid(), "week", {_cat("health"): 100})
        after = scoring.period_score(_uid(), "week", "2026-W33")
        # ...and the score doesn't move.
        assert before["earned"] == after["earned"]
        assert before["possible"] == after["possible"]


def test_mutations_carry_oob_mix_bar_when_targets_set(app, signed_in):
    _unlock_mix(app)
    with app.app_context():
        from onto import mix

        mix.set_targets(_uid(), "week", {_cat("health"): 40})
        gid = _gid("Work out")
    resp = signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    assert b'id="mix-bar"' in resp.data
    assert b'hx-swap-oob="true"' in resp.data


def test_no_mix_bar_without_targets(app, signed_in):
    with app.app_context():
        gid = _gid("Work out")
    resp = signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    assert b'id="mix-bar"' not in resp.data


def test_completed_mix_differs_from_planned(app, signed_in):
    with app.app_context():
        health, hobby = _cat("health"), _cat("hobby")
        g_run, g_cook = _gid("Go for a run"), _gid("Cook something new")
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": g_run})
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": g_cook})
    with app.app_context():
        cid = query("SELECT id FROM commitments WHERE goal_id=?", (g_cook,), one=True)["id"]
    signed_in.post(f"/commitments/{cid}/log")  # only the hobby goal happens
    with app.app_context():
        from onto import mix

        planned = mix.planned_mix(_uid(), "week", "2026-W33")
        completed = mix.completed_mix(_uid(), "week", "2026-W33")
        assert planned == {health: 50.0, hobby: 50.0}
        assert completed == {hobby: 100.0}
