"""The failure these tests prevent: non-admins reshaping the shared taxonomy
or weights, and new taxonomy entries arriving without discovery keywords
(spec 3.4)."""
from onto.db import query
from tests.conftest import sign_up


def test_admin_pages_locked_to_admins(signed_in):
    for path in ("/admin/", "/admin/taxonomy", "/admin/weights"):
        assert signed_in.get(path).status_code == 403


def test_admin_can_add_category_with_terms(app, admin):
    admin.post(
        "/admin/taxonomy/categories",
        data={"name": "Volunteering", "search_terms": "volunteer, community service",
              "example": "Give an hour somewhere"},
    )
    with app.app_context():
        row = query("SELECT * FROM categories WHERE name='Volunteering'", one=True)
        assert row is not None
        assert "volunteer" in row["search_terms_json"]


def test_category_without_terms_rejected(app, admin):
    admin.post("/admin/taxonomy/categories", data={"name": "Nebulous", "search_terms": " "})
    with app.app_context():
        assert query("SELECT id FROM categories WHERE name='Nebulous'", one=True) is None


def test_subcategory_requires_terms(app, admin):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='hobby'", one=True)["id"]
    admin.post(
        "/admin/taxonomy/subcategories",
        data={"category_id": cat, "name": "Skating", "search_terms": ""},
    )
    with app.app_context():
        assert query("SELECT id FROM subcategories WHERE name='Skating'", one=True) is None
    admin.post(
        "/admin/taxonomy/subcategories",
        data={"category_id": cat, "name": "Skating", "search_terms": "skate park, roller rink"},
    )
    with app.app_context():
        assert query("SELECT id FROM subcategories WHERE name='Skating'", one=True) is not None


def test_retired_category_leaves_existing_goals_alone(app, admin):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='hobby'", one=True)["id"]
        n_goals_before = query(
            "SELECT COUNT(*) AS n FROM goals WHERE category_id=?", (cat,), one=True
        )["n"]
    admin.post(f"/admin/taxonomy/categories/{cat}", data={"action": "retire"})
    with app.app_context():
        assert query("SELECT retired FROM categories WHERE id=?", (cat,), one=True)["retired"] == 1
        n_goals_after = query(
            "SELECT COUNT(*) AS n FROM goals WHERE category_id=?", (cat,), one=True
        )["n"]
        assert n_goals_after == n_goals_before
    # Retired categories leave the pickers.
    page = admin.get("/library")
    assert "Hobby —".encode() not in page.data


def test_weights_save_and_inherit(app, admin):
    with app.app_context():
        cat = query("SELECT id FROM categories WHERE slug='health'", one=True)["id"]
        sub = query(
            "SELECT id FROM subcategories WHERE slug='cardio' AND category_id=?", (cat,), one=True
        )["id"]
    admin.post("/admin/weights", data={f"cat-{cat}": "2.5", f"sub-{sub}": "4"})
    with app.app_context():
        assert query("SELECT points FROM categories WHERE id=?", (cat,), one=True)["points"] == 2.5
        assert query("SELECT points FROM subcategories WHERE id=?", (sub,), one=True)["points"] == 4.0
    # Blank puts the subcategory back to inheriting.
    admin.post("/admin/weights", data={f"sub-{sub}": ""})
    with app.app_context():
        assert query("SELECT points FROM subcategories WHERE id=?", (sub,), one=True)["points"] is None


def test_users_have_no_weight_or_taxonomy_routes(client):
    sign_up(client, username="pleb")
    resp = client.post(
        "/admin/taxonomy/categories", data={"name": "Hax", "search_terms": "x"}
    )
    assert resp.status_code == 403
    resp = client.post("/admin/weights", data={"cat-1": "99"})
    assert resp.status_code == 403
