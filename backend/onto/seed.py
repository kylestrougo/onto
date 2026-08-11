"""Idempotent seed data: the category taxonomy and per-user starter goals.

taxonomy() runs at every boot; starter_goals_for() runs at every signup.
Both must stay safe to run twice.
"""
from __future__ import annotations

import json

from .db import execute, query

# The starting taxonomy (spec 3). search terms feed event discovery (3.4);
# examples surface in pickers (11.3). Admin can reshape all of it later.
TAXONOMY: list[dict] = [
    {
        "name": "Health",
        "slug": "health",
        "example": "Work out three times this week",
        "subs": [
            ("Cardio", "cardio", ["running", "5k race", "fun run", "cycling", "spin class"]),
            ("Strength", "strength", ["gym", "strength training", "crossfit", "bootcamp class"]),
            ("Flexibility", "flexibility", ["yoga class", "pilates", "stretching workshop"]),
            ("Sleep", "sleep", ["sleep workshop", "wellness talk"]),
            ("Nutrition", "nutrition", ["healthy cooking class", "nutrition workshop", "farmers market"]),
            ("Mental health", "mental-health", ["meditation class", "mindfulness workshop", "support group"]),
        ],
    },
    {
        "name": "Upskilling",
        "slug": "upskilling",
        "example": "Finish two chapters of the course",
        "subs": [
            ("Course/study", "course-study", ["workshop", "seminar", "lecture", "class"]),
            ("Reading", "reading", ["book club", "author talk", "reading", "library event"]),
            ("Certification", "certification", ["certification workshop", "exam prep", "bootcamp"]),
            ("Language", "language", ["language exchange", "conversation club", "language class"]),
            ("Career", "career", ["networking event", "career fair", "tech meetup", "conference"]),
        ],
    },
    {
        "name": "Social",
        "slug": "social",
        "example": "See a friend this week",
        "subs": [
            ("Friends", "friends", ["trivia night", "game night", "social event"]),
            ("Family", "family", ["family event", "family festival", "kids event"]),
            ("Partner", "partner", ["date night", "couples class", "wine tasting"]),
            ("Community", "community", ["volunteer", "community event", "block party", "town hall"]),
            ("Meeting new people", "meeting-new-people", ["meetup", "singles event", "social mixer", "newcomers"]),
        ],
    },
    {
        "name": "Creative",
        "slug": "creative",
        "example": "Write for 30 minutes, four days",
        "subs": [
            ("Music", "music", ["open mic", "jam session", "music class", "choir"]),
            ("Writing", "writing", ["writing workshop", "poetry reading", "writers group"]),
            ("Photography", "photography", ["photo walk", "photography exhibit", "camera club"]),
            ("Art/craft", "art-craft", ["art class", "pottery class", "craft workshop", "paint night"]),
            ("Performance", "performance", ["improv class", "acting workshop", "dance class", "comedy open mic"]),
        ],
    },
    {
        "name": "Hobby",
        "slug": "hobby",
        "example": "Cook something new this week",
        "subs": [
            ("Outdoors", "outdoors", ["hike", "kayaking", "birding walk", "outdoor adventure"]),
            ("Cooking", "cooking", ["cooking class", "food workshop", "baking class"]),
            ("Games", "games", ["board game night", "chess club", "video game meetup", "trivia"]),
            ("Collecting", "collecting", ["flea market", "collectors show", "vintage market", "card show"]),
            ("Making/building", "making-building", ["maker space", "woodworking class", "repair cafe", "hackathon"]),
        ],
    },
    {
        "name": "Culture",
        "slug": "culture",
        "example": "Visit a museum this month",
        "subs": [
            ("Museums/exhibits", "museums-exhibits", ["museum", "exhibit", "exhibition", "gallery opening", "art show"]),
            ("Live music", "live-music", ["concert", "live music", "gig", "music festival"]),
            ("Film/theater", "film-theater", ["film screening", "movie night", "theater", "play", "musical"]),
            ("Food/dining", "food-dining", ["food festival", "restaurant week", "night market", "food crawl"]),
            ("Neighborhood exploring", "neighborhood-exploring", ["walking tour", "street fair", "open house", "history tour"]),
        ],
    },
    {
        "name": "Admin/Life",
        "slug": "admin-life",
        "example": "Knock out one errand you've been avoiding",
        "subs": [
            ("Home", "home", ["home improvement workshop", "gardening class"]),
            ("Finances", "finances", ["financial literacy workshop", "tax help"]),
            ("Errands", "errands", []),
            ("Health appointments", "health-appointments", ["health fair", "free screening", "blood drive"]),
        ],
    },
]

# Starter library for new accounts (spec 11.2): nothing starts empty. Each is
# (title, kind, category_slug, subcategory_slug, default_target, recurring).
STARTER_GOALS: list[tuple] = [
    ("Work out", "countable", "health", "strength", 3, 1),
    ("Go for a run", "countable", "health", "cardio", 2, 0),
    ("Call a friend or family member", "binary", "social", "family", None, 1),
    ("Read for 30 minutes", "countable", "upskilling", "reading", 4, 0),
    ("Cook something new", "binary", "hobby", "cooking", None, 0),
    ("See some live music", "novelty", "culture", "live-music", None, 0),
    ("Visit a museum or exhibit", "novelty", "culture", "museums-exhibits", None, 0),
    ("Explore a neighborhood I don't know", "binary", "culture", "neighborhood-exploring", None, 0),
    ("Knock out one errand I've been avoiding", "binary", "admin-life", "errands", None, 0),
    ("Try something completely new", "novelty", "social", "meeting-new-people", None, 0),
]


def taxonomy() -> None:
    """Insert the starting taxonomy where it's missing. Idempotent: existing
    rows are never touched, so admin edits survive restarts."""
    for pos, cat in enumerate(TAXONOMY):
        row = query("SELECT id FROM categories WHERE slug = ?", (cat["slug"],), one=True)
        if row:
            cat_id = row["id"]
        else:
            # Category search terms = union of its subcategories' terms.
            all_terms = sorted({t for _, _, terms in cat["subs"] for t in terms})
            cat_id = execute(
                "INSERT INTO categories (name, slug, position, search_terms_json, example)"
                " VALUES (?, ?, ?, ?, ?)",
                (cat["name"], cat["slug"], pos, json.dumps(all_terms), cat["example"]),
            )
        for name, slug, terms in cat["subs"]:
            execute(
                "INSERT OR IGNORE INTO subcategories (category_id, name, slug, search_terms_json)"
                " VALUES (?, ?, ?, ?)",
                (cat_id, name, slug, json.dumps(terms)),
            )


def _ids_for(category_slug: str, subcategory_slug: str) -> tuple[int, int] | None:
    row = query(
        "SELECT s.id AS sub_id, c.id AS cat_id FROM subcategories s"
        " JOIN categories c ON c.id = s.category_id"
        " WHERE c.slug = ? AND s.slug = ?",
        (category_slug, subcategory_slug),
        one=True,
    )
    return (row["cat_id"], row["sub_id"]) if row else None


def _clone_starter(user_id: int, starter: tuple) -> int | None:
    title, kind, cat_slug, sub_slug, target, recurring = starter
    ids = _ids_for(cat_slug, sub_slug)
    if not ids:
        return None
    cat_id, sub_id = ids
    goal_id = execute(
        "INSERT INTO goals (created_by, title, kind, category_id, subcategory_id,"
        " default_target, recurring) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, title, kind, cat_id, sub_id, target, recurring),
    )
    execute(
        "INSERT INTO goal_members (goal_id, user_id, role) VALUES (?, ?, 'owner')",
        (goal_id, user_id),
    )
    return goal_id


def starter_goals_for(user_id: int) -> None:
    """Clone the whole starter library for a fresh account (config-gated).
    Idempotent per user."""
    if query(
        "SELECT 1 FROM goals WHERE created_by = ? LIMIT 1", (user_id,), one=True
    ):
        return
    for starter in STARTER_GOALS:
        _clone_starter(user_id, starter)


def quick_add(user_id: int, title: str) -> int | None:
    """Clone one starter goal by title — the library page's quick-add.
    A second tap of the same title is a no-op."""
    for starter in STARTER_GOALS:
        if starter[0] == title:
            if query(
                "SELECT 1 FROM goals g JOIN goal_members m ON m.goal_id = g.id"
                " WHERE m.user_id = ? AND g.title = ? AND g.retired_at IS NULL",
                (user_id, title),
                one=True,
            ):
                return None
            return _clone_starter(user_id, starter)
    return None
