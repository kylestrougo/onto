"""The failure these tests prevent: an account system that leaks which
usernames exist, silently loses sessions, or lets anyone become admin."""
from onto.db import query
from tests.conftest import sign_up


def test_signup_signs_you_in(client):
    resp = sign_up(client)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"
    page = client.get("/", follow_redirects=True)
    assert page.status_code == 200


def test_signup_rejects_bad_usernames(client):
    for bad in ("ab", "has space", "way" + "y" * 40, "sneaky/slash"):
        resp = client.post("/signup", data={"username": bad, "password": "longenoughpw"})
        assert resp.status_code == 400, bad


def test_signup_rejects_short_password(client):
    resp = client.post("/signup", data={"username": "kyle", "password": "short"})
    assert resp.status_code == 400


def test_username_taken_case_insensitive(client):
    sign_up(client, username="Kyle")
    client.post("/logout")
    resp = client.post("/signup", data={"username": "kyle", "password": "longenoughpw"})
    assert resp.status_code == 400
    assert b"taken" in resp.data


def test_login_wrong_password_matches_unknown_user(client):
    """Unknown username and wrong password must be indistinguishable."""
    sign_up(client, username="kyle")
    client.post("/logout")
    wrong_pw = client.post("/login", data={"username": "kyle", "password": "wrongwrongwrong"})
    unknown = client.post("/login", data={"username": "nobody", "password": "wrongwrongwrong"})
    assert wrong_pw.status_code == unknown.status_code == 401
    # Jinja escapes the apostrophe, so match the fragment before it.
    assert b"Username or password didn" in wrong_pw.data
    assert b"Username or password didn" in unknown.data


def test_login_and_logout(client):
    sign_up(client)
    client.post("/logout")
    resp = client.post("/login", data={"username": "kyle", "password": "longenoughpw"})
    assert resp.status_code == 302
    client.post("/logout")
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_admin_username_promoted_once(app, client):
    sign_up(client, username="admin")
    client.post("/logout")
    sign_up(client, username="admin2", password="longenoughpw")
    with app.app_context():
        assert query("SELECT role FROM users WHERE username='admin'", one=True)["role"] == "admin"
        assert query("SELECT role FROM users WHERE username='admin2'", one=True)["role"] == "user"


def test_signup_rate_limited_per_ip(client):
    for i in range(3):
        sign_up(client, username=f"user{i}")
        client.post("/logout")
    resp = client.post("/signup", data={"username": "user99", "password": "longenoughpw"})
    assert resp.status_code == 400
    assert b"Too many accounts" in resp.data


def test_timezone_captured_and_not_blanked(app, client):
    sign_up(client, timezone="America/New_York")
    with app.app_context():
        row = query("SELECT timezone FROM users WHERE username='kyle'", one=True)
        assert row["timezone"] == "America/New_York"
    # A login without a timezone field must not blank the stored one.
    client.post("/logout")
    client.post("/login", data={"username": "kyle", "password": "longenoughpw"})
    with app.app_context():
        row = query("SELECT timezone FROM users WHERE username='kyle'", one=True)
        assert row["timezone"] == "America/New_York"


def test_open_redirect_blocked(client):
    sign_up(client)
    client.post("/logout")
    resp = client.post(
        "/login",
        data={"username": "kyle", "password": "longenoughpw", "next": "https://evil.example"},
    )
    assert resp.headers["Location"] == "/"
    client.post("/logout")
    resp = client.post(
        "/login",
        data={"username": "kyle", "password": "longenoughpw", "next": "//evil.example"},
    )
    assert resp.headers["Location"] == "/"
