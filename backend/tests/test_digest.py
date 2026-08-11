"""The failure these tests prevent: a notification stating an event fact
that isn't in the corpus with a live link (spec 9.4/10) — smuggled ids,
smuggled dates, dead URLs — or double-sending."""
from unittest.mock import patch

from onto.db import execute, query
from onto.notify import validate


# ── The validator (spec 10.2/10.3) ───────────────────────────────────────


def test_out_of_set_event_id_rejected():
    parsed = {
        "opening": "A good week ahead.",
        "goal_notes": [],
        "event_picks": [{"event_id": 999, "why": "sounds fun"}],
        "closing": "",
    }
    clean = validate.clean(parsed, set(), {1, 2})
    assert clean["event_picks"] == []  # invented id, gone


def test_out_of_set_commitment_id_rejected():
    parsed = {"opening": "", "goal_notes": [{"commitment_id": 5, "text": "nice"}],
              "event_picks": [], "closing": ""}
    assert validate.clean(parsed, {1}, set()) is None  # nothing survived


def test_smuggled_facts_rejected():
    bad_texts = [
        "See you at https://sketchy.example",
        "It's on Saturday at 8pm",
        "Only $15 at the door",
        "Happening March 3 in the park",
        "Come by on the 15th",
        "Doors at 19:30",
        "x" * 400,  # over-length
    ]
    for text in bad_texts:
        parsed = {"opening": text, "goal_notes": [], "event_picks": [], "closing": ""}
        assert validate.clean(parsed, set(), set()) is None, text


def test_clean_prose_passes():
    parsed = {
        "opening": "Two down, one to go — steady week.",
        "goal_notes": [{"commitment_id": 1, "text": "One more run does it."}],
        "event_picks": [{"event_id": 2, "why": "You said you wanted live music."}],
        "closing": "Take the small win.",
    }
    clean = validate.clean(parsed, {1}, {2})
    assert clean == parsed


def test_tainted_why_keeps_pick_loses_blurb():
    parsed = {"opening": "ok week", "goal_notes": [],
              "event_picks": [{"event_id": 2, "why": "This Friday at 7pm!"}], "closing": ""}
    clean = validate.clean(parsed, set(), {2})
    assert clean["event_picks"] == [{"event_id": 2, "why": ""}]


# ── Composition pipeline ─────────────────────────────────────────────────


def _seed_user_week(app, signed_in):
    with app.app_context():
        gid = query("SELECT id FROM goals WHERE title='Cook something new'", one=True)["id"]
    signed_in.post("/week/2026-W33/commitments", data={"goal_id": gid})
    with app.app_context():
        return query("SELECT * FROM users WHERE username='kyle'", one=True)


def _seed_suggestion(uid):
    execute("INSERT OR IGNORE INTO sources (name, kind, tier, config_json)"
            " VALUES ('T','ics',2,'{}')")
    src = query("SELECT id FROM sources WHERE name='T'", one=True)["id"]
    cat = query("SELECT id FROM categories WHERE slug='culture'", one=True)["id"]
    eid = execute(
        "INSERT INTO events (source_id, external_id, dedupe_key, title, url, starts_at,"
        " category_id, is_free, tier) VALUES (?, 'e1', 'e1', 'Rooftop Jazz',"
        " 'https://venue/x', datetime('now', '+2 days'), ?, 1, 2)",
        (src, cat),
    )
    execute(
        "INSERT INTO suggestions (user_id, event_id, score, reason)"
        " VALUES (?, ?, 4.0, 'matches')",
        (uid, eid),
    )
    return eid


def test_dead_url_event_never_reaches_the_llm(app, signed_in):
    user = _seed_user_week(app, signed_in)
    with app.app_context():
        _seed_suggestion(user["id"])
        from onto.notify import compose

        seen_by_llm = {}

        def fake_generate(system, prompt, intent="generic", **kw):
            seen_by_llm["prompt"] = prompt
            return {"opening": "hi", "goal_notes": [], "event_picks": [], "closing": ""}

        with patch("onto.notify.compose.check_url", return_value=False), \
             patch("onto.llm.generate", fake_generate):
            facts, prose = compose.compose(user)
        assert facts["suggestions"] == []  # dropped before composition
        assert "Rooftop Jazz" not in seen_by_llm["prompt"]


def test_fallback_after_two_bad_generations(app, signed_in):
    user = _seed_user_week(app, signed_in)
    with app.app_context():
        from onto.notify import compose

        def bad_generate(system, prompt, intent="generic", **kw):
            return {"opening": "Party on Friday at 9pm!", "goal_notes": [],
                    "event_picks": [], "closing": ""}

        with patch("onto.notify.compose.check_url", return_value=True), \
             patch("onto.llm.generate", bad_generate):
            facts, prose = compose.compose(user)
        # Both attempts failed validation → deterministic fallback prose.
        assert "Friday" not in prose["opening"]
        assert "0 of 1" in prose["opening"]


def test_digest_renders_facts_from_db_rows(app, signed_in):
    user = _seed_user_week(app, signed_in)
    with app.app_context():
        _seed_suggestion(user["id"])
        from onto.notify import compose, send

        with patch("onto.notify.compose.check_url", return_value=True), \
             patch("onto.llm.generate", side_effect=Exception("no llm")), \
             patch("onto.notify.compose.llm_compose", return_value=None):
            facts, prose = compose.compose(user)
        subject, text, html = send.render_digest(user, facts, prose)
        assert "Rooftop Jazz" in html and "https://venue/x" in html
        assert "Cook something new" in text
        assert "from T" in html  # source attribution


# ── Sending ──────────────────────────────────────────────────────────────


def _quiet_compose():
    return patch(
        "onto.notify.send.compose.compose",
        return_value=(
            {"week_label": "wk", "commitments": [], "score": {"earned": 0, "possible": 0, "percent": 0},
             "gap_names": [], "friends": [], "suggestions": []},
            {"opening": "hello", "goal_notes": [], "event_picks": [], "closing": ""},
        ),
    )


def test_send_due_respects_frequency_and_never_double_sends(app, signed_in):
    with app.app_context():
        from onto.notify import send

        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        send.ensure_prefs(uid)
        # Make the user due right now: daily, hour 0.
        execute("UPDATE notification_prefs SET frequency='daily', send_hour=0")
        with _quiet_compose():
            first = send.send_due_digests()
            second = send.send_due_digests()
        assert first["sent"] == 1
        assert second["sent"] == 0 and second["skipped"] == 1  # last_sent_on guard
        n = query("SELECT COUNT(*) AS n FROM digests", one=True)["n"]
        assert n == 1  # in-app copy (default channel) exactly once


def test_off_frequency_never_sends(app, signed_in):
    with app.app_context():
        from onto.notify import send

        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        send.ensure_prefs(uid)
        execute("UPDATE notification_prefs SET frequency='off'")
        with _quiet_compose():
            result = send.send_due_digests()
        assert result == {"sent": 0, "skipped": 0, "failed": 0}


def test_email_channel_dry_run_and_inapp_copy(app, signed_in, caplog):
    import logging

    with app.app_context():
        from onto.notify import send

        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        send.ensure_prefs(uid)
        execute(
            "UPDATE notification_prefs SET frequency='daily', send_hour=0,"
            " channel='both', email='kyle@example.com'"
        )
        with _quiet_compose(), caplog.at_level(logging.INFO, logger="onto.notify.send"):
            result = send.send_due_digests()
        assert result["sent"] == 1
        assert any("DRY RUN" in r.message and "kyle@example.com" in r.message
                   for r in caplog.records)
        assert query("SELECT COUNT(*) AS n FROM digests", one=True)["n"] == 1


def test_unsubscribe_link(app, signed_in):
    with app.app_context():
        from onto.notify import send

        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        token = send.prefs_for(uid)["unsub_token"]
    resp = signed_in.get(f"/unsub/{token}")
    assert b"no more notes" in resp.data
    with app.app_context():
        assert query("SELECT frequency FROM notification_prefs", one=True)["frequency"] == "off"
    # A bogus token changes nothing and says so.
    resp = signed_in.get("/unsub/not-a-token")
    assert b"nothing changed" in resp.data


def test_digests_page_shows_and_marks_read(app, signed_in):
    with app.app_context():
        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        execute(
            "INSERT INTO digests (user_id, subject, html) VALUES (?, 'Your week — wk',"
            " '<p>hello there</p>')",
            (uid,),
        )
    page = signed_in.get("/digests")
    assert b"hello there" in page.data
    with app.app_context():
        assert query("SELECT read_at FROM digests", one=True)["read_at"] is not None


def test_email_channel_without_address_falls_back_inapp_once(app, signed_in):
    """Channel 'email' with no address must still deliver (in-app) and must
    not re-deliver every hour."""
    with app.app_context():
        from onto.notify import send

        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        send.ensure_prefs(uid)
        execute(
            "UPDATE notification_prefs SET frequency='daily', send_hour=0,"
            " channel='email', email=''"
        )
        with _quiet_compose():
            first = send.send_due_digests()
            second = send.send_due_digests()
        assert first["sent"] == 1
        assert second["skipped"] == 1  # the day is spent
        assert query("SELECT COUNT(*) AS n FROM digests", one=True)["n"] == 1


def test_smtp_failure_delivers_inapp_and_never_duplicates(app, signed_in):
    from unittest.mock import patch as _patch

    with app.app_context():
        from onto.notify import send

        uid = query("SELECT id FROM users WHERE username='kyle'", one=True)["id"]
        send.ensure_prefs(uid)
        execute(
            "UPDATE notification_prefs SET frequency='daily', send_hour=0,"
            " channel='email', email='kyle@example.com'"
        )
        with _quiet_compose(), _patch("onto.notify.send._send_email", return_value=False):
            first = send.send_due_digests()
            second = send.send_due_digests()
        assert first["failed"] == 1  # the email leg is flagged for the log
        assert second["skipped"] == 1  # but the note is not re-sent hourly
        # The user still got their note, exactly once, in-app.
        assert query("SELECT COUNT(*) AS n FROM digests", one=True)["n"] == 1


def test_settings_reject_email_channel_without_address(app, signed_in):
    resp = signed_in.post(
        "/settings",
        data={"frequency": "weekly", "channel": "email", "email": "not-an-address",
              "send_hour": "9", "send_dow": "0"},
        follow_redirects=True,
    )
    assert b"kept them in the app" in resp.data
    with app.app_context():
        prefs = query("SELECT * FROM notification_prefs", one=True)
        assert prefs["channel"] == "inapp"


def test_settings_save_email_prefs_and_send_day(app, signed_in):
    signed_in.post(
        "/settings",
        data={"frequency": "weekly", "channel": "both", "email": "kyle@example.com",
              "send_hour": "18", "send_dow": "6"},
    )
    with app.app_context():
        prefs = query("SELECT * FROM notification_prefs", one=True)
        assert (prefs["channel"], prefs["email"]) == ("both", "kyle@example.com")
        assert (prefs["send_hour"], prefs["send_dow"]) == (18, 6)
        # The weekly due-gate honours the chosen day.
        from datetime import datetime

        from onto.notify.send import _is_due

        sunday_evening = datetime(2026, 8, 16, 19, 0)  # Sunday, weekday 6
        monday_evening = datetime(2026, 8, 17, 19, 0)
        assert _is_due(prefs, sunday_evening) is True
        assert _is_due(prefs, monday_evening) is False
