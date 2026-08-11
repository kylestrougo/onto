"""The failure these tests prevent: an empty first screen (spec 11.2 says
nothing starts empty) or a taxonomy that drifts on every boot."""
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
