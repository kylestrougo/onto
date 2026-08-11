-- Onto schema. Every statement is IF NOT EXISTS and the whole file is executed
-- at boot; columns added after first deploy go through db._ensure_column.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    -- IANA name captured from the browser at signup/login; '' means unknown
    -- and DEFAULT_TZ applies. Every period boundary is computed in this zone.
    timezone TEXT NOT NULL DEFAULT '',
    boroughs_json TEXT NOT NULL DEFAULT '[]',
    -- Last week key the rollover cron materialised recurring goals for.
    last_rollover_key TEXT NOT NULL DEFAULT '',
    -- Progressive-disclosure override: show every feature regardless of unlocks.
    show_everything INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Runtime-editable settings that must survive restart and be changeable
-- without a deploy (LLM model chain, per-intent overrides, ...). JSON values.
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Daily quota buckets: 'signup:<ip>', 'user:<id>'. Coarse by design — a quota
-- guard, not a DDoS defence. Pruned by housekeeping.
CREATE TABLE IF NOT EXISTS usage_counters (
    subject TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject, day)
);

-- ── Taxonomy (admin-managed; users pick, never create — spec 3.3) ────────

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL DEFAULT 0,
    -- Admin-set scoring weight (spec 5.1). Flat 1.0 default = no starting bias.
    points REAL NOT NULL DEFAULT 1.0,
    -- Event-discovery keywords (spec 3.4). JSON array of strings.
    search_terms_json TEXT NOT NULL DEFAULT '[]',
    -- One-line plain-English example shown in pickers (spec 11.3).
    example TEXT NOT NULL DEFAULT '',
    retired INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subcategories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    -- NULL inherits the category's points.
    points REAL,
    search_terms_json TEXT NOT NULL DEFAULT '[]',
    retired INTEGER NOT NULL DEFAULT 0,
    UNIQUE (category_id, slug)
);

-- ── Goals: the library. A goal is never scored; its commitments are. ─────

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('countable', 'binary', 'deadline', 'window', 'open', 'novelty')),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    subcategory_id INTEGER REFERENCES subcategories(id),
    -- For countable goals; open goals get a target per-commitment instead.
    default_target INTEGER,
    -- Recurring is a flag, not a kind: the base kind still decides scoring.
    recurring INTEGER NOT NULL DEFAULT 0,
    -- Event suggestions are opt-in per goal, default off (spec 11.6).
    discovery_enabled INTEGER NOT NULL DEFAULT 0,
    visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'friends', 'public')),
    notes TEXT NOT NULL DEFAULT '',
    -- Retired goals keep their whole history (spec 1.7).
    retired_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_goals_owner ON goals(created_by);

-- Joint ownership for shared goals (spec 7.4). The creator gets an 'owner'
-- row; invited friends get 'member' rows in Phase 4.
CREATE TABLE IF NOT EXISTS goal_members (
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (goal_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_goal_members_user ON goal_members(user_id);

-- ── Commitments: a goal dropped into a specific period (spec 1, terms) ───

CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id),
    period_kind TEXT NOT NULL CHECK (period_kind IN ('week', 'month')),
    -- '2026-W33' (ISO week, Monday start) or '2026-08'. Computed in the
    -- user's timezone; opaque and chronologically sortable everywhere else.
    period_key TEXT NOT NULL,
    -- Required for countable/open; per-commitment so "run more" can be
    -- 3 runs one week and 5 the next (spec: Open type).
    target INTEGER,
    -- For deadline goals.
    due_date TEXT,
    -- Set when the commitment came from an accepted event suggestion
    -- (spec 8.7) or a novelty goal got its "what".
    event_id INTEGER,
    suggestion_id INTEGER,
    completed_at TEXT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- Same goal can be active in many periods, once per period (spec 1.4).
    UNIQUE (goal_id, period_kind, period_key)
);
CREATE INDEX IF NOT EXISTS idx_commitments_period ON commitments(period_kind, period_key);
CREATE INDEX IF NOT EXISTS idx_commitments_goal ON commitments(goal_id);

-- Honor-system check-offs (spec 6). One row per log; countable progress is
-- the row count. user_id records who, for shared goals.
CREATE TABLE IF NOT EXISTS completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commitment_id INTEGER NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    note TEXT NOT NULL DEFAULT '',
    photo_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_completions_commitment ON completions(commitment_id);

-- One-way progressive-disclosure unlocks (spec 11.1).
CREATE TABLE IF NOT EXISTS user_flags (
    user_id INTEGER NOT NULL REFERENCES users(id),
    flag TEXT NOT NULL,
    unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, flag)
);

-- ── Category mix (spec 4): a planning aid, never a scoring input ─────────
-- Target ratios per user and period kind. Rows are only present for
-- categories with a target; ratios needn't total 100 — the remainder is
-- "anything" (spec 4.4).
CREATE TABLE IF NOT EXISTS mix_targets (
    user_id INTEGER NOT NULL REFERENCES users(id),
    period_kind TEXT NOT NULL CHECK (period_kind IN ('week', 'month')),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    percent INTEGER NOT NULL CHECK (percent BETWEEN 1 AND 100),
    PRIMARY KEY (user_id, period_kind, category_id)
);

-- ── Social (spec 7) ──────────────────────────────────────────────────────

-- Mutual friendships. The pair is normalised (lo < hi) so one row serves
-- both directions and the UNIQUE constraint can't be dodged by swapping
-- requester and addressee.
CREATE TABLE IF NOT EXISTS friendships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_lo INTEGER NOT NULL REFERENCES users(id),
    user_hi INTEGER NOT NULL REFERENCES users(id),
    requester_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (user_lo < user_hi),
    UNIQUE (user_lo, user_hi)
);

-- Invitations into a goal (spec 7.5). Accepting inserts a goal_members row —
-- joint ownership, joint completion (spec 7.4).
CREATE TABLE IF NOT EXISTS goal_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    from_id INTEGER NOT NULL REFERENCES users(id),
    to_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (goal_id, to_id)
);

-- Write-time feed rows (spec 7.3). Visibility is enforced at read time by
-- joining the goal — a goal later made private disappears from history too.
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    verb TEXT NOT NULL CHECK (verb IN ('committed', 'completed', 'goal_shared', 'suggestion_accepted')),
    goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE,
    commitment_id INTEGER,
    event_id INTEGER,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity(user_id, id DESC);

-- ── Event discovery corpus (spec 8) ──────────────────────────────────────
-- One shared, city-wide corpus; research runs on a schedule, never per user
-- (spec 8.3). Every event keeps its provenance (spec 8.4).

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    -- Which adapter fetches it: 'nyc_open_data' | 'ics' | 'jsonld' | 'llm_research'
    kind TEXT NOT NULL CHECK (kind IN ('nyc_open_data', 'ics', 'jsonld', 'llm_research')),
    -- Provenance tier: 1 official/open data, 2 structured venue feed,
    -- 3 LLM web research. Copied onto every event it produces.
    tier INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 3),
    -- Adapter settings: url, field mapping, default category slug, ...
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    quarantined_at TEXT,
    -- Adapter failures in a row; >= 5 auto-disables (unattended cron safety).
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    -- Distinct-user flags roll up here; repeat offenders get quarantined
    -- (spec 10.6).
    flag_count INTEGER NOT NULL DEFAULT 0,
    last_run_at TEXT,
    last_status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    -- The source's own id for the event; the upsert key per source.
    external_id TEXT NOT NULL,
    -- slug(title) + local date; cross-source duplicate detection.
    dedupe_key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    -- Non-negotiable: every event links to where it came from (spec 10.5).
    url TEXT NOT NULL,
    starts_at TEXT NOT NULL,          -- UTC 'YYYY-MM-DD HH:MM:SS'
    ends_at TEXT,
    venue_name TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    borough TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT 'NYC',
    lat REAL, lon REAL,
    -- NULL category = the admin's uncategorized queue, not a dropped event.
    category_id INTEGER REFERENCES categories(id),
    subcategory_id INTEGER REFERENCES subcategories(id),
    cost_cents INTEGER,
    is_free INTEGER NOT NULL DEFAULT 0,
    tier INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'expired', 'quarantined', 'removed')),
    raw_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_verified_at TEXT,
    verify_ok INTEGER,
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_events_status_start ON events(status, starts_at);
CREATE INDEX IF NOT EXISTS idx_events_cat_start ON events(category_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_events_dedupe ON events(dedupe_key);
