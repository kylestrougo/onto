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
    click.echo(f"housekeeping: pruned {n} old usage counters")


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
