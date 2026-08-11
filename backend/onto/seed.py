"""Idempotent seed data: category taxonomy and per-user starter goals.

Phase 0 ships the hooks; Phase 1 fills them. Both must stay safe to run on
every boot and every signup.
"""
from __future__ import annotations


def taxonomy() -> None:
    """Insert the starting category taxonomy if it isn't there. Idempotent."""
    # Filled in Phase 1.


def starter_goals_for(user_id: int) -> None:
    """Clone the starter goal library for a fresh account (spec 11.2)."""
    # Filled in Phase 1.
