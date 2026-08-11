"""The failures these tests prevent: a taxonomy that drifts on every boot,
a new account silently pre-filled with goals the user never chose, and a
quick-add that duplicates on a double-tap."""
from onto.db import query
from tests.conftest import sign_up


def test_taxonomy_seeded_and_idempotent(app):
    with app.app_context():
        from onto import seed

        cats = query("SELECT * FROM categories ORDER BY position")
        assert [c["name"] for c in cats] == [
            "Health", "Upskilling", "Social", "Creative", "Hobby", "Culture", "Admin/Life",
        ]
        n_subs = query("SELECT COUNT(*) AS n FROM subcategories", one=True)["n"]
        assert n_subs == 35
        # Every subcategory carries discovery keywords except deliberate blanks.
        empties = query(
            "SELECT name FROM subcategories WHERE search_terms_json = '[]'"
        )
        assert [r["name"] for r in empties] == ["Errands"]

        seed.taxonomy()  # second run: nothing duplicated
        assert query("SELECT COUNT(*) AS n FROM categories", one=True)["n"] == 7
        assert query("SELECT COUNT(*) AS n FROM subcategories", one=True)["n"] == n_subs


def test_new_account_starts_empty_with_quick_adds(app, client):
    """Production default: no pre-seeded goals; the library offers the
    starters as one-tap quick-adds, and a double tap adds nothing twice."""
    app.config["STARTER_GOALS_ON_SIGNUP"] = False
    sign_up(client)
    with app.app_context():
        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        assert query(
            "SELECT COUNT(*) AS n FROM goals WHERE created_by = ?", (uid,), one=True
        )["n"] == 0

    page = client.get("/library")
    assert b"Quick add" in page.data and b"Work out" in page.data
    assert b"+ Add a goal" in page.data

    for _ in range(2):  # the second tap is a no-op
        client.post("/goals/quick-add", data={"title": "Work out"})
    with app.app_context():
        rows = query("SELECT title, kind FROM goals WHERE created_by = ?", (uid,))
        assert [(r["title"], r["kind"]) for r in rows] == [("Work out", "countable")]

    # Once added, it leaves the quick-add row (the hidden input is the
    # quick-add form's fingerprint) but shows as a goal row.
    page = client.get("/library")
    assert b'value="Work out"' not in page.data
    assert b"Work out" in page.data


def test_new_account_gets_starter_library(app, signed_in):
    with app.app_context():
        rows = query(
            "SELECT g.title, g.kind FROM goals g JOIN users u ON u.id = g.created_by"
            " WHERE u.username = 'kyle'"
        )
        assert len(rows) == 10
        kinds = {r["kind"] for r in rows}
        assert "novelty" in kinds and "countable" in kinds and "binary" in kinds
        # Owner membership rows exist for each.
        n = query(
            "SELECT COUNT(*) AS n FROM goal_members m JOIN users u ON u.id = m.user_id"
            " WHERE u.username = 'kyle' AND m.role = 'owner'",
            one=True,
        )["n"]
        assert n == 10


def test_starter_library_not_duplicated_for_existing_user(app, client):
    sign_up(client)
    with app.app_context():
        from onto import seed

        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        seed.starter_goals_for(uid)  # e.g. a re-login hook firing again
        n = query("SELECT COUNT(*) AS n FROM goals WHERE created_by = ?", (uid,), one=True)["n"]
        assert n == 10
