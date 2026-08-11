"""The failure these tests prevent: the tolerant parser — the riskiest code
in the LLM layer — regressing on the shapes free models actually emit."""
import pytest

from onto.llm import parse_json_loose


def test_clean_json():
    assert parse_json_loose('{"a": 1}') == {"a": 1}


def test_fenced_json():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}


def test_chatty_preamble_and_suffix():
    text = 'Sure! Here is your JSON:\n{"a": 1}\nHope that helps!'
    assert parse_json_loose(text) == {"a": 1}


def test_smart_quotes_and_trailing_comma():
    text = '{“title”: “Jazz Night”, "n": 2,}'
    assert parse_json_loose(text) == {"title": "Jazz Night", "n": 2}


def test_literal_newline_inside_string():
    text = '{"a": "line one\nline two"}'
    assert parse_json_loose(text) == {"a": "line one\\nline two"} or parse_json_loose(
        text
    ) == {"a": "line one\nline two"}


def test_rejects_garbage():
    for bad in ("", "no json here", "[1, 2, 3]"):
        with pytest.raises(ValueError):
            parse_json_loose(bad)


def test_chain_fallback_and_stats(app):
    """Transport failure falls through to the next model; every attempt is
    recorded."""
    from unittest.mock import patch

    from onto import llm
    from onto.db import query

    calls = []

    def fake_post(model, system, user, max_tokens, json_mode, temperature=None, timeout=None):
        calls.append(model)
        if model == "bad/model":
            raise llm.LLMError("HTTP 502")
        return '{"ok": true}'

    with app.app_context():
        llm.set_config_json(llm.CONFIG_KEY_CHAIN, ["bad/model", "good/model"])
        with patch.object(llm, "_post", fake_post):
            result = llm.generate("sys", "user", intent="generic")
        assert result == {"ok": True}
        assert calls == ["bad/model", "good/model"]
        rows = query("SELECT model, ok FROM model_stats ORDER BY id")
        assert [(r["model"], r["ok"]) for r in rows] == [("bad/model", 0), ("good/model", 1)]


def test_unparseable_gets_second_chance_then_falls_through(app):
    from unittest.mock import patch

    from onto import llm

    calls = []

    def fake_post(model, system, user, max_tokens, json_mode, temperature=None, timeout=None):
        calls.append((model, json_mode))
        if model == "chatty/model":
            return "I cannot do JSON today"
        return '{"ok": 1}'

    with app.app_context():
        llm.set_config_json(llm.CONFIG_KEY_CHAIN, ["chatty/model", "good/model"])
        with patch.object(llm, "_post", fake_post):
            result = llm.generate("sys", "user")
        # Two attempts on the chatty model (second without json_mode), then on.
        assert calls == [("chatty/model", True), ("chatty/model", False), ("good/model", True)]
        assert result == {"ok": 1}
