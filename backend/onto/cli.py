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
