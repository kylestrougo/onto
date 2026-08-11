"""The failure these tests prevent: a deploy that looks up but serves nothing."""


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_home_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_static_served(client):
    resp = client.get("/static/css/onto.css")
    assert resp.status_code == 200


def test_404_renders(signed_in):
    resp = signed_in.get("/no-such-page")
    assert resp.status_code == 404
