"""Cron-facing commands.

Keeping the scheduler out of the Flask process is deliberate: a scheduler
thread would sit in memory all day on a Pi that's already running several
services. cron costs nothing when it isn't running. Every job here is
idempotent and due-gated: cron fires dumbly, the command decides what's due.
"""
from __future__ import annotations

import click
from flask.cli import with_appcontext

from . import ratelimit


@click.command("housekeeping")
@with_appcontext
def housekeeping() -> None:
    """Nightly prune of unbounded tables. Safe to re-run."""
    n = ratelimit.prune_counters()
    click.echo(f"housekeeping: pruned {n} old usage counters")


def init_app(app) -> None:
    app.cli.add_command(housekeeping)
