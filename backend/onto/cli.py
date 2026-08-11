"""Cron-facing commands.

Keeping the scheduler out of the Flask process is deliberate: a scheduler
thread would sit in memory all day on a Pi that's already running several
services. cron costs nothing when it isn't running. Every job here is
idempotent and due-gated: cron fires dumbly, the command decides what's due.
"""
from __future__ import annotations

import click
from flask.cli import with_appcontext

from . import periods, ratelimit
from .db import execute, get_db, query


@click.command("housekeeping")
@with_appcontext
def housekeeping() -> None:
    """Nightly prune of unbounded tables. Safe to re-run."""
    n = ratelimit.prune_counters()
    # Events long past keep no purpose; flagged ones stay for the audit trail.
    db = get_db()
    cur = db.execute(
        "DELETE FROM events WHERE status IN ('expired', 'removed')"
        " AND COALESCE(ends_at, starts_at) < datetime('now', '-30 days')"
        " AND id NOT IN (SELECT event_id FROM event_flags)"
        if _table_exists(db, "event_flags")
        else "DELETE FROM events WHERE status IN ('expired', 'removed')"
        " AND COALESCE(ends_at, starts_at) < datetime('now', '-30 days')"
    )
    db.execute("DELETE FROM model_stats WHERE created_at < datetime('now', '-30 days')")
    db.commit()
    from .discovery import suggestions as sugg

    expired = sugg.expire_stale()
    click.echo(
        f"housekeeping: pruned {n} counters, {cur.rowcount} stale events,"
        f" expired {expired} suggestions"
    )


def _table_exists(db, name: str) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


@click.command("ingest")
@click.option("--source", "source_name", default=None, help="Run one source by name.")
@with_appcontext
def ingest_cmd(source_name: str | None) -> None:
    """Fetch enabled sources into the shared event corpus. Idempotent —
    re-running updates rather than duplicates."""
    from .ingest import base as ingest_base

    where = "enabled = 1 AND quarantined_at IS NULL AND kind != 'llm_research'"
    args: tuple = ()
    if source_name:
        where += " AND name = ?"
        args = (source_name,)
    sources = query(f"SELECT * FROM sources WHERE {where}", args)
    if not sources:
        click.echo("ingest: no matching enabled sources")
        return
    for source in sources:
        counts = ingest_base.run_source(source)
        click.echo(f"ingest: {source['name']}: {counts}")


@click.command("verify-events")
@click.option("--limit", default=100, show_default=True)
@with_appcontext
def verify_events_cmd(limit: int) -> None:
    """Nightly: expire past events, then re-check the stalest URLs."""
    from .ingest import verify

    expired = verify.expire_past_events()
    result = verify.verify_batch(limit)
    click.echo(f"verify-events: expired {expired}, checked {result['checked']},"
               f" ok {result['ok']}, bad {result['bad']}")


@click.command("research")
@with_appcontext
def research_cmd() -> None:
    """Tier-3 LLM research via SearXNG (twice weekly, city-wide, one shared
    corpus — spec 8.3). Creates its default source row on first run so the
    quarantine machinery covers it like any other source."""
    from flask import current_app

    if not current_app.config["SEARXNG_URL"]:
        click.echo("research: ONTO_SEARXNG_URL not set; skipping")
        return
    execute(
        "INSERT OR IGNORE INTO sources (name, kind, tier, config_json)"
        " VALUES ('LLM research', 'llm_research', 3, '{}')"
    )
    from .ingest import base as ingest_base

    sources = query(
        "SELECT * FROM sources WHERE kind = 'llm_research' AND enabled = 1"
        " AND quarantined_at IS NULL"
    )
    if not sources:
        click.echo("research: the research source is disabled or quarantined")
        return
    for source in sources:
        counts = ingest_base.run_source(source)
        click.echo(f"research: {source['name']}: {counts}")


@click.command("match")
@with_appcontext
def match_cmd() -> None:
    """Nightly: match corpus events to users' discovery-enabled goals
    (spec 8.5). Due-gated by the per-user weekly suggestion cap."""
    from .discovery import matching

    made = matching.run_all()
    click.echo(f"match: created {made} suggestions")


# ── Model chain automation (curio port) ─────────────────────────────────
# The free catalogue churns without notice — models get renamed and retired,
# and a chain that worked last week can be entirely dead this week, which
# takes discovery and digests down with it. refresh-chain repairs a dead
# chain and refuses to touch a working one: latency is the one thing a
# benchmark can judge, and it says nothing about whether the prose is any
# good. Choosing on quality stays a human decision, made from /admin/models.

BENCH_COMMITMENT_IDS = {1, 2}
BENCH_EVENT_IDS = {10}


def _bench_prompt() -> tuple[str, str]:
    """The canonical probe: one fixed digest fact sheet, identical for every
    model, so latencies are comparable and the gate matches production."""
    from .notify.compose import _digest_prompt

    facts = {
        "week_label": "Aug 10 – Aug 16",
        "commitments": [
            {"id": 1, "title": "Work out", "kind": "countable", "target": 3,
             "logged": 2, "completed_at": None},
            {"id": 2, "title": "Cook something new", "kind": "binary", "target": None,
             "logged": 0, "completed_at": None},
        ],
        "score": {"earned": 0.67, "possible": 2.0, "percent": 33},
        "gap_names": ["Culture"],
        "friends": [],
        "suggestions": [
            {"event_id": 10, "event_title": "Rooftop Jazz", "starts_at": "2099-09-01 19:00:00",
             "venue_name": "Pier 4", "borough": "Brooklyn", "is_free": 1,
             "reason": "matches seeing live music"}
        ],
    }
    return _digest_prompt(facts)


def _check_contract(parsed) -> str | None:
    """Why a probe response is unusable, or None when production would take it.

    Latency alone is a trap: a model that answers fast with invented event
    ids or date-laced prose is worse than a slower one that gets it right.
    The gate is the exact validator the digest pipeline trusts.
    """
    from .notify import validate

    if not isinstance(parsed, dict):
        return "not an object"
    if validate.clean(parsed, BENCH_COMMITMENT_IDS, BENCH_EVENT_IDS) is None:
        return "nothing survived the digest validator"
    return None


def _bench(ids, repeat, echo=None):
    """Time each model on a real digest generation. Returns [(ms|None, id, failure)].

    Sequential on purpose: firing these in parallel measures the free tier's
    rate limiter rather than the models.
    """
    from statistics import median

    from . import llm

    system, user = _bench_prompt()
    results = []
    for mid in ids:
        latencies, failure = [], None
        for _ in range(repeat):
            r = llm.generate_raw(mid, system, user, intent="bench")
            if not r["ok"]:
                failure = (r["error"] or "failed")[:60]
                break
            reason = _check_contract(r["parsed"])
            if reason:
                failure = f"off-contract: {reason}"
                break
            latencies.append(r["latency_ms"])
        if failure:
            results.append((None, mid, failure))
            if echo:
                echo(f"  x {mid} — {failure}")
        else:
            ms = int(median(latencies))
            results.append((ms, mid, None))
            if echo:
                echo(f"  ok {mid} — {ms}ms")
    return results


def _usable(results):
    # (ms, id, None) tuples sort fastest-first, ties broken alphabetically.
    return sorted([r for r in results if r[0] is not None])


@click.command("refresh-chain")
@click.option("--force", is_flag=True, help="Re-rank even when the current chain still works.")
@click.option("--repeat", type=int, default=3, help="Calls per model; median is used.")
@click.option("--top", type=int, default=3, help="How many models to keep in the chain.")
@click.option("--dry-run", is_flag=True, help="Report what would change, change nothing.")
@with_appcontext
def refresh_chain_command(force, repeat, top, dry_run):
    """Repair the model chain when it has rotted. Intended for cron.

    Checks the chain still works and only rebuilds it when it doesn't. It
    will NOT reorder a working chain to chase a faster model. Better a stale
    chain than none: an empty chain fails every generation, so both failure
    paths leave the current chain alone and exit nonzero.
    """
    from .llm import CONFIG_KEY_CHAIN, get_chain, list_free_models, set_config_json

    current = get_chain()
    click.echo(f"chain: {' -> '.join(current) or '(empty)'}")

    working = _usable(_bench(current, repeat, echo=click.echo)) if current else []
    if working and not force:
        click.echo(f"chain is healthy ({len(working)}/{len(current)} usable) — leaving it alone")
        return

    click.echo("chain is dead — rebuilding from the catalogue" if not force else "forced re-rank")
    catalogue = [m["id"] for m in list_free_models()]
    if not catalogue:
        click.echo("catalogue returned nothing — leaving the chain alone")
        raise SystemExit(1)

    ranked = _usable(_bench(catalogue, repeat, echo=click.echo))
    if not ranked:
        # Better a stale chain than none: an empty chain fails every call.
        click.echo("nothing in the catalogue passed — leaving the chain alone")
        raise SystemExit(1)

    new = [mid for _, mid, _ in ranked[:top]]
    if new == current:
        click.echo("no change")
        return
    if dry_run:
        click.echo(f"would set: {' -> '.join(new)}")
        return
    set_config_json(CONFIG_KEY_CHAIN, new)
    click.echo(f"chain updated: {' -> '.join(new)}")


@click.command("bench-models")
@click.option("--all-free", is_flag=True, help="Bench the whole free catalogue, not just the chain.")
@click.option("--repeat", type=int, default=3, help="Calls per model; median is used.")
@with_appcontext
def bench_models_command(all_free, repeat):
    """Manually rank models by usable-then-fast. Prints a paste-ready chain."""
    from .llm import get_chain, list_free_models

    ids = [m["id"] for m in list_free_models()] if all_free else get_chain()
    if not ids:
        click.echo("nothing to bench")
        return
    good = _usable(_bench(ids, repeat, echo=click.echo))
    if not good:
        click.echo("nothing passed")
        raise SystemExit(1)
    click.echo("")
    click.echo("fastest usable chain: " + ",".join(mid for _, mid, _ in good[:3]))


@click.command("send-digests")
@with_appcontext
def send_digests_cmd() -> None:
    """Hourly: send the weekly/daily notes that are due, per user, on the
    user's own clock. A missed run catches up next hour."""
    from .notify import send

    result = send.send_due_digests()
    click.echo(
        f"send-digests: sent {result['sent']}, skipped {result['skipped']},"
        f" failed {result['failed']}"
    )


def _materialize(user_id: int, period_kind: str, key: str) -> int:
    """Create this period's commitments for one user's recurring goals.
    INSERT OR IGNORE + the UNIQUE constraint make this safe to re-run."""
    if period_kind == "week":
        # Recurring deadline goals are skipped on purpose: a repeating "by
        # the 20th" has no inferable next date, so re-adding it is a human's
        # call from the week view, not the robot's.
        kind_clause = "g.kind IN ('countable', 'binary', 'open', 'novelty')"
    else:
        kind_clause = "g.kind = 'window'"
    rows = query(
        "SELECT g.* FROM goals g JOIN goal_members m ON m.goal_id = g.id"
        f" WHERE m.user_id = ? AND g.recurring = 1 AND g.retired_at IS NULL AND {kind_clause}",
        (user_id,),
    )
    made = 0
    db = get_db()
    for goal in rows:
        target = None
        if goal["kind"] in ("countable", "open"):
            # Last period's target wins — "run more" that settled on 4 stays
            # at 4 — falling back to the goal's default.
            last = query(
                "SELECT target FROM commitments WHERE goal_id = ? ORDER BY id DESC LIMIT 1",
                (goal["id"],),
                one=True,
            )
            target = (last["target"] if last else None) or goal["default_target"]
            if not target:
                continue  # an open goal with no history has no number to copy
        cur = db.execute(
            "INSERT OR IGNORE INTO commitments (goal_id, period_kind, period_key,"
            " target, created_by) VALUES (?, ?, ?, ?, ?)",
            (goal["id"], period_kind, key, target, goal["created_by"]),
        )
        made += cur.rowcount
    db.commit()
    return made


@click.command("rollover")
@with_appcontext
def rollover() -> None:
    """Materialise recurring goals into each user's new week and month.

    Cron runs this hourly; per user it fires only when their local week or
    month key has moved past users.last_rollover_key ('<week>|<month>').
    A Pi that slept through Monday catches up on the next tick. Shared goals
    can be reached from several members' rollovers — the commitments UNIQUE
    constraint makes that a no-op.
    """
    made = 0
    for u in query("SELECT * FROM users"):
        wk = periods.current_key("week", u["timezone"])
        mk = periods.current_key("month", u["timezone"])
        stored = (u["last_rollover_key"] or "|").split("|")
        old_wk, old_mk = stored[0], stored[1] if len(stored) > 1 else ""
        if wk == old_wk and mk == old_mk:
            continue
        if wk != old_wk:
            made += _materialize(u["id"], "week", wk)
        if mk != old_mk:
            made += _materialize(u["id"], "month", mk)
        execute(
            "UPDATE users SET last_rollover_key = ? WHERE id = ?", (f"{wk}|{mk}", u["id"])
        )
    click.echo(f"rollover: created {made} commitments")


def init_app(app) -> None:
    app.cli.add_command(housekeeping)
    app.cli.add_command(rollover)
    app.cli.add_command(ingest_cmd)
    app.cli.add_command(verify_events_cmd)
    app.cli.add_command(research_cmd)
    app.cli.add_command(match_cmd)
    app.cli.add_command(send_digests_cmd)
    app.cli.add_command(refresh_chain_command)
    app.cli.add_command(bench_models_command)
