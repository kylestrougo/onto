"""The two invariants this file pins (curio's, ported): refresh-chain
repairs a chain that has stopped working, and it declines to rewrite one
that still works just because something else measured faster. Either way it
never leaves the chain empty."""
import pytest

from onto import cli as cli_mod
from onto import llm


@pytest.fixture()
def bench(monkeypatch):
    """Stub the benchmark: map model id → latency ms, or None for 'failed'."""
    calls = []

    def install(table):
        def fake(ids, repeat, echo=None):
            calls.append(list(ids))
            return [
                (table.get(i), i, None if table.get(i) else "stubbed failure")
                for i in ids
            ]

        monkeypatch.setattr(cli_mod, "_bench", fake)

    install.calls = calls
    return install


@pytest.fixture()
def catalogue(monkeypatch):
    # refresh-chain imports list_free_models lazily from onto.llm, so the
    # patch target is the llm module, not cli.
    def install(ids):
        monkeypatch.setattr("onto.llm.list_free_models", lambda: [{"id": i} for i in ids])

    return install


def _set_chain(app, models):
    with app.app_context():
        llm.set_config_json(llm.CONFIG_KEY_CHAIN, models)


def _get_chain(app):
    with app.app_context():
        return llm.get_chain()


def _run(app, *args):
    return app.test_cli_runner().invoke(args=["refresh-chain", *args])


class TestLeavesWorkingChainsAlone:
    def test_healthy_chain_is_not_touched(self, app, bench, catalogue):
        _set_chain(app, ["slow:free"])
        bench({"slow:free": 9000, "fast:free": 800})
        catalogue(["fast:free", "slow:free"])
        result = _run(app)
        assert result.exit_code == 0
        assert "healthy" in result.output
        assert _get_chain(app) == ["slow:free"]

    def test_force_re_ranks_a_healthy_chain(self, app, bench, catalogue):
        _set_chain(app, ["slow:free"])
        bench({"slow:free": 9000, "fast:free": 800})
        catalogue(["fast:free", "slow:free"])
        result = _run(app, "--force", "--top", "2")
        assert result.exit_code == 0
        assert _get_chain(app) == ["fast:free", "slow:free"]


class TestRepairsBrokenChains:
    def test_dead_chain_is_rebuilt_fastest_first(self, app, bench, catalogue):
        _set_chain(app, ["retired:free"])
        bench({"mid:free": 3000, "quick:free": 900, "slow:free": 8000})
        catalogue(["mid:free", "quick:free", "slow:free"])
        result = _run(app, "--top", "2")
        assert result.exit_code == 0
        assert "chain updated" in result.output
        assert _get_chain(app) == ["quick:free", "mid:free"]

    def test_dry_run_reports_without_writing(self, app, bench, catalogue):
        _set_chain(app, ["retired:free"])
        bench({"quick:free": 900})
        catalogue(["quick:free"])
        result = _run(app, "--dry-run")
        assert result.exit_code == 0
        assert "would set" in result.output
        assert _get_chain(app) == ["retired:free"]


class TestRefusesToMakeThingsWorse:
    def test_keeps_a_dead_chain_when_nothing_else_works(self, app, bench, catalogue):
        _set_chain(app, ["retired:free"])
        bench({})  # everything fails
        catalogue(["a:free", "b:free"])
        result = _run(app)
        assert result.exit_code == 1
        assert _get_chain(app) == ["retired:free"]

    def test_keeps_the_chain_when_the_catalogue_is_empty(self, app, bench, catalogue):
        _set_chain(app, ["retired:free"])
        bench({})
        catalogue([])
        result = _run(app)
        assert result.exit_code == 1
        assert _get_chain(app) == ["retired:free"]


class TestBenchGate:
    """The contract gate is production's own digest validator."""

    def test_contract_accepts_what_the_validator_accepts(self, app):
        with app.app_context():
            good = {"opening": "Nice steady week so far.", "goal_notes": [],
                    "event_picks": [{"event_id": 10, "why": "you wanted live music"}],
                    "closing": ""}
            assert cli_mod._check_contract(good) is None

    def test_contract_rejects_invented_ids_and_smuggled_dates(self, app):
        with app.app_context():
            invented = {"opening": "", "goal_notes": [],
                        "event_picks": [{"event_id": 999, "why": "x"}], "closing": ""}
            assert cli_mod._check_contract(invented) is not None
            dated = {"opening": "See you Friday at 8pm!", "goal_notes": [],
                     "event_picks": [], "closing": ""}
            assert cli_mod._check_contract(dated) is not None
            assert cli_mod._check_contract("not a dict") == "not an object"

    def test_bench_temperature_mirrors_digest(self):
        assert llm._TEMPERATURES["bench"] == llm._TEMPERATURES["digest"]

    def test_bench_short_circuits_on_failure_and_ranks_fastest_first(self, app, monkeypatch):
        calls = []

        def fake_raw(model, system, user, intent="admin_test", **kw):
            calls.append(model)
            if model == "bad:free":
                return {"ok": False, "raw": None, "parsed": None,
                        "latency_ms": 5, "error": "HTTP 429"}
            latency = 900 if model == "quick:free" else 3000
            return {"ok": True, "raw": "{}", "latency_ms": latency,
                    "parsed": {"opening": "A fine week.", "goal_notes": [],
                               "event_picks": [], "closing": ""}, "error": None}

        monkeypatch.setattr("onto.llm.generate_raw", fake_raw)
        with app.app_context():
            results = cli_mod._bench(["bad:free", "mid:free", "quick:free"], repeat=2)
        # bad:free probed once (short-circuit), others twice.
        assert calls.count("bad:free") == 1
        assert calls.count("mid:free") == 2
        ranked = cli_mod._usable(results)
        assert [mid for _, mid, _ in ranked] == ["quick:free", "mid:free"]
