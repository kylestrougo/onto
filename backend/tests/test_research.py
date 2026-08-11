"""The failure these tests prevent: the tier-3 pipeline letting an invented
event into the corpus — bad date, past date, unknown borough, or a URL that
doesn't answer (spec 10.1-10.4 at ingest time)."""
from unittest.mock import patch

from onto.db import execute, query


def _setup_research(app):
    execute(
        "INSERT OR IGNORE INTO sources (name, kind, tier, config_json)"
        " VALUES ('LLM research', 'llm_research', 3, '{}')"
    )
    return query("SELECT * FROM sources WHERE kind='llm_research'", one=True)


FAKE_PAGE = "<html><body>" + "Concerts this month in Brooklyn. " * 30 + "</body></html>"


def _llm_returns(events):
    def fake_generate(system, user, intent="generic", **kwargs):
        return {"events": events}

    return fake_generate


def _run_with(app, events, url_ok=True):
    from onto.ingest import base as ingest_base

    class FakeResp:
        text = FAKE_PAGE

    with patch("onto.ingest.searxng.search",
               return_value=[{"url": "https://blog.example/events", "title": "x"}]), \
         patch("onto.ingest.base.http_get", return_value=FakeResp()), \
         patch("onto.ingest.adapters.llm_research.check_url", return_value=url_ok), \
         patch("onto.llm.generate", _llm_returns(events)), \
         patch("onto.ingest.adapters.llm_research._thin_subcategories",
               return_value=query(
                   "SELECT s.id, s.slug, s.name, s.search_terms_json, c.slug AS cat_slug"
                   " FROM subcategories s JOIN categories c ON c.id=s.category_id"
                   " WHERE s.slug='live-music'")):
        source = _setup_research(app)
        return ingest_base.run_source(source)


def _events_in_corpus():
    return query("SELECT * FROM events")


def test_valid_extraction_lands_as_tier3(app):
    with app.app_context():
        app.config["SEARXNG_URL"] = "http://searx.local"
        counts = _run_with(app, [{
            "title": "Waterfront Jazz Night", "date": "2099-09-01", "time": "19:00",
            "venue": "Pier 4", "borough": "Brooklyn",
            "url": "https://blog.example/events", "cost": "free",
            "description": "Jazz on the pier.",
        }])
        assert counts["inserted"] == 1
        ev = _events_in_corpus()[0]
        assert ev["tier"] == 3
        assert ev["starts_at"] == "2099-09-01 19:00:00"
        assert ev["is_free"] == 1


def test_invented_details_dropped(app):
    with app.app_context():
        app.config["SEARXNG_URL"] = "http://searx.local"
        bad_events = [
            {"title": "No Date Show", "date": "sometime soon", "url": "https://blog.example/events"},
            {"title": "Past Show", "date": "2001-01-01", "url": "https://blog.example/events"},
            {"title": "Wrong Borough", "date": "2099-09-01", "borough": "Gotham",
             "url": "https://blog.example/events"},
            {"title": "", "date": "2099-09-01", "url": "https://blog.example/events"},
            {"title": "No URL Show", "date": "2099-09-01", "url": ""},
        ]
        counts = _run_with(app, bad_events)
        assert counts["inserted"] == 0
        assert _events_in_corpus() == []


def test_unverifiable_url_dropped(app):
    """A URL other than the page itself must answer 200 or the event dies."""
    with app.app_context():
        app.config["SEARXNG_URL"] = "http://searx.local"
        counts = _run_with(app, [{
            "title": "Suspicious Show", "date": "2099-09-01",
            "url": "https://some-other-site.example/made-up",
        }], url_ok=False)
        assert counts["inserted"] == 0
        assert _events_in_corpus() == []


def test_research_skipped_without_searxng(app):
    with app.app_context():
        app.config["SEARXNG_URL"] = ""
        from onto.ingest import base as ingest_base

        source = _setup_research(app)
        counts = ingest_base.run_source(source)
        assert counts.get("inserted", 0) == 0
        assert "error" not in counts  # a skip, not a failure
