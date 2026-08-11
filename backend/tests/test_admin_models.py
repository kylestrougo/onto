"""The failure these tests prevent: non-admins steering the shared model
chain, the chain being saved empty, and curio's override bug — an intent
whose override is silently dropped on save while the UI appears to accept
it."""
from unittest.mock import patch

from onto import llm
from onto.db import query
from onto.views.admin import _test_intents


def _chain(app):
    with app.app_context():
        return llm.get_chain()


def _overrides(app):
    with app.app_context():
        return llm.get_overrides()


FAKE_CATALOGUE = [
    {"id": "a:free", "name": "A", "context_length": 8192, "description": ""},
    {"id": "b:free", "name": "B", "context_length": 65536, "description": ""},
]


def _page(client):
    with patch("onto.llm.list_free_models", return_value=list(FAKE_CATALOGUE)):
        return client.get("/admin/models")


def test_locked_to_admins(signed_in):
    assert signed_in.get("/admin/models").status_code == 403
    for path in ("/admin/models/chain", "/admin/models/overrides", "/admin/models/test"):
        assert signed_in.post(path, data={}).status_code == 403


def test_page_renders_chain_and_catalogue(app, admin):
    page = _page(admin)
    assert page.status_code == 200
    assert b"a:free" in page.data
    assert b"The chain, in order" in page.data


def test_page_survives_openrouter_outage(app, admin):
    with patch("onto.llm.list_free_models", side_effect=ConnectionError("down")):
        page = admin.get("/admin/models")
    assert page.status_code == 200
    assert b"Couldn" in page.data  # flash-style error, page intact


def test_add_move_remove(app, admin):
    with app.app_context():
        llm.set_config_json(llm.CONFIG_KEY_CHAIN, ["a:free", "b:free"])
    admin.post("/admin/models/chain", data={"action": "add", "model": "c:free"})
    assert _chain(app) == ["a:free", "b:free", "c:free"]
    admin.post("/admin/models/chain", data={"action": "up", "model": "c:free"})
    assert _chain(app) == ["a:free", "c:free", "b:free"]
    admin.post("/admin/models/chain", data={"action": "down", "model": "a:free"})
    assert _chain(app) == ["c:free", "a:free", "b:free"]
    admin.post("/admin/models/chain", data={"action": "remove", "model": "a:free"})
    assert _chain(app) == ["c:free", "b:free"]
    # Adding a duplicate is a no-op.
    admin.post("/admin/models/chain", data={"action": "add", "model": "b:free"})
    assert _chain(app) == ["c:free", "b:free"]


def test_never_saves_an_empty_chain(app, admin):
    with app.app_context():
        llm.set_config_json(llm.CONFIG_KEY_CHAIN, ["only:free"])
    resp = admin.post(
        "/admin/models/chain", data={"action": "remove", "model": "only:free"},
        follow_redirects=True,
    )
    assert _chain(app) == ["only:free"]
    assert b"at least one model" in resp.data


def test_chain_caps_at_eight(app, admin):
    with app.app_context():
        llm.set_config_json(llm.CONFIG_KEY_CHAIN, [f"m{i}:free" for i in range(8)])
    admin.post("/admin/models/chain", data={"action": "add", "model": "extra:free"})
    assert len(_chain(app)) == 8
    assert "extra:free" not in _chain(app)


def test_overrides_filtered_to_known_intents_and_round_trip(app, admin):
    """The curio regression: EVERY intent in the allow-list must survive a
    save round trip; unknown intents are dropped."""
    intents = list(_test_intents())
    assert set(intents) == {"digest", "research_extract"}
    data = {f"override-{i}": "strong:free" for i in intents}
    data["override-bogus"] = "x:free"
    admin.post("/admin/models/overrides", data=data)
    saved = _overrides(app)
    assert saved == {i: "strong:free" for i in intents}
    # Blank deletes.
    admin.post("/admin/models/overrides", data={"override-digest": ""})
    assert _overrides(app) == {}


def test_override_leads_and_chain_backs_it_up(app):
    with app.app_context():
        llm.set_config_json(llm.CONFIG_KEY_CHAIN, ["a:free", "b:free"])
        llm.set_config_json(llm.CONFIG_KEY_OVERRIDES, {"digest": "b:free"})
        assert llm.chain_for("digest") == ["b:free", "a:free"]
        assert llm.chain_for("research_extract") == ["a:free", "b:free"]


def test_every_test_intent_has_a_working_prompt_builder(app):
    """Guards arity drift: each allow-listed intent must build (system, user)."""
    with app.app_context():
        for intent, build in _test_intents().items():
            system, user = build()
            assert system and user, intent


def test_live_test_renders_ok_and_failure(app, admin):
    def ok_post(model, system, user, max_tokens, json_mode, temperature=None, timeout=None):
        return '{"opening": "A fine week.", "goal_notes": [], "event_picks": [], "closing": ""}'

    with patch.object(llm, "_post", ok_post), \
         patch("onto.llm.list_free_models", return_value=list(FAKE_CATALOGUE)):
        page = admin.post(
            "/admin/models/test", data={"model": "a:free", "intent": "digest"}
        )
    assert page.status_code == 200
    assert b"OK" in page.data and b"A fine week." in page.data

    def bad_post(model, system, user, max_tokens, json_mode, temperature=None, timeout=None):
        raise llm.LLMError("HTTP 429: rate limited")

    with patch.object(llm, "_post", bad_post), \
         patch("onto.llm.list_free_models", return_value=list(FAKE_CATALOGUE)):
        page = admin.post(
            "/admin/models/test", data={"model": "a:free", "intent": "research_extract"}
        )
    assert page.status_code == 200  # failure is content, not a status code
    assert b"Failed" in page.data and b"429" in page.data
    # Both probes were recorded to model_stats.
    with app.app_context():
        n = query("SELECT COUNT(*) AS n FROM model_stats WHERE intent='admin_test'", one=True)["n"]
        assert n == 2


def test_unknown_intent_rejected(app, admin):
    resp = admin.post(
        "/admin/models/test", data={"model": "a:free", "intent": "bogus"},
        follow_redirects=False,
    )
    assert resp.status_code == 302  # bounced with a flash, no LLM call
    with app.app_context():
        assert query("SELECT COUNT(*) AS n FROM model_stats", one=True)["n"] == 0


def test_stats_rollup_percentiles(app):
    with app.app_context():
        from onto.db import execute

        for ms in (100, 200, 300, 400):
            execute(
                "INSERT INTO model_stats (model, intent, ok, latency_ms) VALUES"
                " ('m:free', 'digest', 1, ?)", (ms,),
            )
        execute(
            "INSERT INTO model_stats (model, intent, ok, latency_ms, error) VALUES"
            " ('m:free', 'digest', 0, 50, 'HTTP 429')"
        )
        rows = llm.stats_rollup(7)
        assert len(rows) == 1
        s = rows[0]
        assert s["calls"] == 5
        assert s["ok_rate"] == 0.8
        assert s["p50_ms"] == 300  # index-pick on [100,200,300,400]
        assert s["last_error"] == "HTTP 429"
