"""The failure these tests prevent: recurring goals double-materialising,
skipping a user whose week hasn't turned in their timezone, or resurrecting
retired goals."""
from onto.db import execute, query


def _run_rollover(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["rollover"])
    assert result.exit_code == 0, result.output
    return result.output


def _uid():
    return query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]


def test_rollover_materialises_recurring_goals(app, signed_in):
    with app.app_context():
        # Starter library: 'Work out' (countable 3x) and 'Call a friend or
        # family member' (binary) are recurring.
        out = _run_rollover(app)
        rows = query(
            "SELECT g.title, cm.target, cm.period_kind FROM commitments cm"
            " JOIN goals g ON g.id = cm.goal_id"
        )
        titles = {r["title"] for r in rows}
        assert titles == {"Work out", "Call a friend or family member"}
        workout = next(r for r in rows if r["title"] == "Work out")
        assert workout["target"] == 3
        assert all(r["period_kind"] == "week" for r in rows)
        assert "created 2" in out


def test_rollover_is_idempotent(app, signed_in):
    with app.app_context():
        _run_rollover(app)
        out = _run_rollover(app)  # same hour, nothing due
        assert "created 0" in out
        n = query("SELECT COUNT(*) AS n FROM commitments", one=True)["n"]
        assert n == 2


def test_rollover_fires_again_next_week(app, signed_in):
    with app.app_context():
        _run_rollover(app)
        # Pretend that run happened last week: shift its commitments into the
        # past and reset the due-gate, so the current week is fresh again.
        execute("UPDATE users SET last_rollover_key = '2020-W01|2020-01'")
        execute("UPDATE commitments SET period_key = '2020-W01'")
        # Give 'Work out' a custom target that week; this week's copy inherits it.
        execute("UPDATE commitments SET target = 5 WHERE target = 3")
        out = _run_rollover(app)
        assert "created 2" in out
        latest = query(
            "SELECT cm.target FROM commitments cm JOIN goals g ON g.id=cm.goal_id"
            " WHERE g.title='Work out' ORDER BY cm.id DESC LIMIT 1",
            one=True,
        )
        assert latest["target"] == 5  # last period's target wins


def test_rollover_skips_retired_and_nonrecurring(app, signed_in):
    with app.app_context():
        execute(
            "UPDATE goals SET retired_at = datetime('now') WHERE title = 'Work out'"
        )
        _run_rollover(app)
        titles = {
            r["title"]
            for r in query(
                "SELECT g.title FROM commitments cm JOIN goals g ON g.id = cm.goal_id"
            )
        }
        assert "Work out" not in titles
        assert "Go for a run" not in titles  # not recurring


def test_rollover_materialises_recurring_window_goal_monthly(app, signed_in):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='culture'", one=True)["id"]
    signed_in.post(
        "/goals",
        data={"title": "A museum every month", "kind": "window", "category_id": cat,
              "recurring": "1"},
    )
    with app.app_context():
        _run_rollover(app)
        row = query(
            "SELECT cm.period_kind FROM commitments cm JOIN goals g ON g.id=cm.goal_id"
            " WHERE g.title = 'A museum every month'",
            one=True,
        )
        assert row is not None and row["period_kind"] == "month"


def test_rollover_respects_user_timezone(app, signed_in):
    """A user whose local week already turned gets the new week's key even
    when UTC disagrees — the key is computed on their clock."""
    with app.app_context():
        _run_rollover(app)
        stored = query("SELECT last_rollover_key FROM users", one=True)["last_rollover_key"]
        from onto import periods

        wk = periods.current_key("week", "America/New_York")
        mk = periods.current_key("month", "America/New_York")
        assert stored == f"{wk}|{mk}"
