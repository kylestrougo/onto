"""The failure these tests prevent: the corpus filling with duplicates,
uncategorisable events silently vanishing, or one flaky source crashing the
unattended cron."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from onto.db import execute, query

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    @property
    def text(self):
        return self._body.decode()

    def json(self):
        return json.loads(self._body)


def _fake_http(filename):
    body = (FIXTURES / filename).read_bytes()

    def fake_get(url, **kwargs):
        return FakeResponse(body)

    return fake_get


def _add_source(name, kind, tier, config):
    return execute(
        "INSERT INTO sources (name, kind, tier, config_json) VALUES (?, ?, ?, ?)",
        (name, kind, tier, json.dumps(config)),
    )


def _run(name):
    from onto.ingest import base

    source = query("SELECT * FROM sources WHERE name = ?", (name,), one=True)
    return base.run_source(source)


def test_ics_adapter_pipeline(app):
    with app.app_context():
        _add_source(
            "Example Hall", "ics", 2,
            {"url": "https://x/cal.ics", "borough": "Manhattan",
             "category_hint": "culture/live-music", "venue_name": "Example Hall"},
        )
        with patch("onto.ingest.base.http_get", _fake_http("venue.ics")):
            counts = _run("Example Hall")
        # 3 events with dates land; the date-less one is invalid.
        assert counts["inserted"] == 3
        assert counts["invalid"] == 1
        jazz = query("SELECT * FROM events WHERE title='Jazz Quartet Night'", one=True)
        # TZID America/New_York 20:00 EDT → 00:00 UTC next day.
        assert jazz["starts_at"] == "2099-09-16 00:00:00"
        assert jazz["borough"] == "Manhattan"
        assert jazz["url"] == "https://example-hall.org/events/jazz-quartet"
        assert jazz["tier"] == 2
        # The hint categorised it culture/live-music.
        cat = query("SELECT slug FROM categories WHERE id=?", (jazz["category_id"],), one=True)
        assert cat["slug"] == "culture"
        # Poetry open mic has no URL of its own → feed URL, still linkable.
        poetry = query("SELECT url FROM events WHERE title='Poetry Open Mic'", one=True)
        assert poetry["url"] == "https://x/cal.ics"


def test_jsonld_adapter_pipeline(app):
    with app.app_context():
        _add_source(
            "Example Museum", "jsonld", 2,
            {"url": "https://example-museum.org/whats-on", "borough": "Brooklyn"},
        )
        with patch("onto.ingest.base.http_get", _fake_http("venue.html")):
            counts = _run("Example Museum")
        assert counts["inserted"] == 2  # Person node and broken block skipped
        sym = query("SELECT * FROM events WHERE title='Symphony Under the Stars'", one=True)
        assert sym["is_free"] == 1
        assert sym["venue_name"] == "Garden Amphitheater"
        maps_ev = query("SELECT * FROM events WHERE title='Ancient Maps Exhibit'", one=True)
        assert maps_ev["cost_cents"] == 2500
        # Keyword categorisation: "exhibit" hits Culture/Museums-exhibits.
        sub = query(
            "SELECT slug FROM subcategories WHERE id=?", (maps_ev["subcategory_id"],), one=True
        )
        assert sub["slug"] == "museums-exhibits"


def test_socrata_adapter_pipeline(app):
    with app.app_context():
        _add_source(
            "NYC Parks", "nyc_open_data", 1,
            {
                "url": "https://data.cityofnewyork.us/resource/x.json",
                "map": {
                    "external_id": "event_id", "title": "event_name",
                    "starts_at": "start_date_time", "ends_at": "end_date_time",
                    "venue_name": "event_location", "borough": "event_borough",
                    "description": "snippet",
                },
                "event_url": "https://www.nycgovparks.org/events",
            },
        )
        with patch("onto.ingest.base.http_get", _fake_http("socrata.json")):
            counts = _run("NYC Parks")
        assert counts["inserted"] == 2
        assert counts["invalid"] == 1  # no start time
        run = query("SELECT * FROM events WHERE title LIKE '%5K Fun Run%'", one=True)
        assert run["tier"] == 1
        assert run["borough"] == "Manhattan"
        # "5k" + "fun run" keywords → Health/Cardio.
        sub = query("SELECT slug FROM subcategories WHERE id=?", (run["subcategory_id"],), one=True)
        assert sub["slug"] == "cardio"


def test_reingest_updates_not_duplicates(app):
    with app.app_context():
        _add_source("Example Hall", "ics", 2, {"url": "https://x/cal.ics"})
        with patch("onto.ingest.base.http_get", _fake_http("venue.ics")):
            _run("Example Hall")
            counts = _run("Example Hall")
        assert counts["inserted"] == 0
        assert counts["updated"] == 3
        n = query("SELECT COUNT(*) AS n FROM events", one=True)["n"]
        assert n == 3


def test_cross_source_dedupe_keeps_lower_tier(app):
    with app.app_context():
        from onto.ingest.base import RawEvent, upsert_event

        _add_source("Feed A", "ics", 2, {})
        _add_source("Official B", "nyc_open_data", 1, {})
        feed = query("SELECT * FROM sources WHERE name='Feed A'", one=True)
        official = query("SELECT * FROM sources WHERE name='Official B'", one=True)

        raw = RawEvent(
            external_id="a1", title="Harvest Festival", url="https://a/1",
            starts_at="2099-10-01 12:00:00",
        )
        assert upsert_event(feed, raw) == "inserted"
        # Same event arrives from the official source → replaces the feed copy.
        raw2 = RawEvent(
            external_id="b1", title="Harvest Festival!", url="https://b/1",
            starts_at="2099-10-01 14:00:00",
        )
        assert upsert_event(official, raw2) == "inserted"
        active = query("SELECT * FROM events WHERE status='active'")
        assert len(active) == 1 and active[0]["tier"] == 1
        # And a re-arrival of the feed copy is skipped, not resurrected.
        assert upsert_event(feed, raw) == "skipped"


def test_uncategorizable_lands_in_admin_queue_not_dropped(app, admin):
    with app.app_context():
        from onto.ingest.base import RawEvent, upsert_event

        _add_source("Feed A", "ics", 2, {})
        feed = query("SELECT * FROM sources WHERE name='Feed A'", one=True)
        upsert_event(feed, RawEvent(
            external_id="x", title="Zorbulating the Frobnicator", url="https://a/x",
            starts_at="2099-11-01 12:00:00",
        ))
        row = query("SELECT category_id, status FROM events", one=True)
        assert row["category_id"] is None and row["status"] == "active"
    page = admin.get("/admin/events")
    assert b"Zorbulating" in page.data
    assert b"Needs a category" in page.data


def test_five_consecutive_errors_auto_disable(app):
    with app.app_context():
        _add_source("Flaky", "ics", 2, {"url": "https://down.example/cal.ics"})

        def boom(url, **kwargs):
            raise ConnectionError("refused")

        with patch("onto.ingest.base.http_get", boom):
            for i in range(5):
                _run("Flaky")
        row = query("SELECT enabled, consecutive_errors FROM sources WHERE name='Flaky'", one=True)
        assert row["consecutive_errors"] == 5
        assert row["enabled"] == 0  # cron leaves it alone from now on


def test_verify_marks_dead_links_and_expires_past(app):
    with app.app_context():
        from onto.ingest import verify
        from onto.ingest.base import RawEvent, upsert_event

        _add_source("Feed A", "ics", 2, {})
        feed = query("SELECT * FROM sources WHERE name='Feed A'", one=True)
        upsert_event(feed, RawEvent(
            external_id="past", title="Yesterday Fair", url="https://a/past",
            starts_at="2001-01-01 12:00:00",
        ))
        upsert_event(feed, RawEvent(
            external_id="future", title="Tomorrow Fair", url="https://a/future",
            starts_at="2099-01-01 12:00:00",
        ))
        assert verify.expire_past_events() == 1
        with patch("onto.ingest.verify.check_url", return_value=False):
            result = verify.verify_batch()
        assert result == {"checked": 1, "ok": 0, "bad": 1}
        future = query("SELECT verify_ok FROM events WHERE external_id='future'", one=True)
        assert future["verify_ok"] == 0
        past = query("SELECT status FROM events WHERE external_id='past'", one=True)
        assert past["status"] == "expired"


def test_admin_source_crud(app, admin):
    resp = admin.post(
        "/admin/sources",
        data={"name": "Test Feed", "kind": "ics", "tier": "2",
              "config_json": '{"url": "https://x/cal.ics"}'},
    )
    assert resp.status_code == 302
    with app.app_context():
        row = query("SELECT * FROM sources WHERE name='Test Feed'", one=True)
        assert row["tier"] == 2 and row["enabled"] == 1
    # Bad JSON rejected.
    admin.post("/admin/sources", data={"name": "Broken", "kind": "ics", "tier": "2",
                                       "config_json": "{not json"})
    with app.app_context():
        assert query("SELECT id FROM sources WHERE name='Broken'", one=True) is None
    page = admin.get("/admin/sources")
    assert b"Test Feed" in page.data


def test_admin_source_test_button_is_a_dry_run(app, admin):
    """The Test button fetches and parses but writes no events and leaves
    the error counter alone — success and failure both render as content."""
    with app.app_context():
        sid = _add_source(
            "Example Hall", "ics", 2,
            {"url": "https://x/cal.ics", "borough": "Manhattan",
             "category_hint": "culture/live-music", "venue_name": "Example Hall"},
        )
    with patch("onto.ingest.base.http_get", _fake_http("venue.ics")):
        page = admin.post(f"/admin/sources/{sid}/test")
    assert b"3 usable events" in page.data
    assert b"Jazz Quartet Night" in page.data
    with app.app_context():
        # Dry run: the corpus is untouched.
        assert query("SELECT COUNT(*) AS n FROM events", one=True)["n"] == 0

    def boom(url, **kwargs):
        raise ValueError("feed exploded")

    with patch("onto.ingest.base.http_get", boom):
        page = admin.post(f"/admin/sources/{sid}/test")
    assert b"feed exploded" in page.data
    with app.app_context():
        # A failed test never counts toward auto-disable.
        row = query("SELECT consecutive_errors, enabled FROM sources WHERE id=?", (sid,), one=True)
        assert (row["consecutive_errors"], row["enabled"]) == (0, 1)
