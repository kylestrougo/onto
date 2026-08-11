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
