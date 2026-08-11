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
