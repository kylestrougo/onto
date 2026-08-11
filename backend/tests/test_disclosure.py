"""The failure these tests prevent: features unlocking too early (spec 11.1
wants a quiet first run) or yanking themselves back once revealed."""
from onto.db import query


def _gid(title):
    return query("SELECT id FROM goals WHERE title = ?", (title,), one=True)["id"]


def _uid(username):
    return query("SELECT id FROM users WHERE username = ?", (username,), one=True)["id"]


def test_fresh_user_has_no_unlocks(app, signed_in):
    with app.app_context():
        n = query("SELECT COUNT(*) AS n FROM user_flags", one=True)["n"]
        assert n == 0


def test_mix_unlocks_after_three_completions_across_two_weeks(app, signed_in):
    with app.app_context():
        g1, g2 = _gid("Cook something new"), _gid("Knock out one errand I've been avoiding")

    # Three completions in ONE week: not yet.
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": g1})
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": g2})
    with app.app_context():
        c1 = query("SELECT id FROM commitments WHERE goal_id=?", (g1,), one=True)["id"]
        c2 = query("SELECT id FROM commitments WHERE goal_id=?", (g2,), one=True)["id"]
    signed_in.post(f"/commitments/{c1}/log")
    signed_in.post(f"/commitments/{c2}/log")
    with app.app_context():
        assert query("SELECT 1 FROM user_flags WHERE flag='mix'", one=True) is None

    # A completion in a second week trips it.
    signed_in.post("/week/2026-W34/commitments", data={"goal_id": g1})
    with app.app_context():
        c3 = query(
            "SELECT id FROM commitments WHERE goal_id=? AND period_key='2026-W34'",
            (g1,), one=True,
        )["id"]
    signed_in.post(f"/commitments/{c3}/log")
    with app.app_context():
        uid = _uid("kyle")
        assert query(
            "SELECT 1 FROM user_flags WHERE user_id=? AND flag='mix'", (uid,), one=True
        )
        assert query(
            "SELECT 1 FROM user_flags WHERE user_id=? AND flag='months'", (uid,), one=True
        )


def test_unlock_is_one_way(app, signed_in):
    with app.app_context():
        from onto import disclosure

        uid = _uid("kyle")
        assert disclosure.unlock(uid, "mix") is True
        assert disclosure.unlock(uid, "mix") is False  # already held, no re-announce
        assert query("SELECT COUNT(*) AS n FROM user_flags WHERE user_id=?", (uid,), one=True)["n"] == 1
