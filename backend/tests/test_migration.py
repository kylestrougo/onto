"""The failure this test prevents: a column added to schema.sql that never
reaches an already-deployed database, because CREATE TABLE IF NOT EXISTS is a
no-op for existing tables. _ensure_column is the entire migration story and
must stay idempotent."""
import sqlite3

from onto.db import _ensure_column


def test_ensure_column_adds_and_is_idempotent(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE things (id INTEGER PRIMARY KEY)")
    db.commit()

    _ensure_column(db, "things", "flavour", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(db, "things", "flavour", "TEXT NOT NULL DEFAULT ''")  # second run: no-op

    cols = {row[1] for row in db.execute("PRAGMA table_info(things)")}
    assert "flavour" in cols
    db.execute("INSERT INTO things (id) VALUES (1)")
    assert db.execute("SELECT flavour FROM things").fetchone()[0] == ""


def test_boot_upgrades_old_shaped_db(tmp_path):
    """Build a database missing a later column, then boot the app against it."""
    path = tmp_path / "deployed.db"
    conn = sqlite3.connect(path)
    # An "old" users table, as a first deploy would have created it.
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
        " role TEXT NOT NULL DEFAULT 'user')"
    )
    conn.commit()
    conn.close()

    from onto import create_app
    from onto.config import Config

    class Cfg(Config):
        DATABASE = str(path)
        SECRET_KEY = "test-secret"
        TESTING = True

    app = create_app(Cfg)
    with app.app_context():
        from onto.db import get_db

        cols = {r[1] for r in get_db().execute("PRAGMA table_info(users)")}
        tables = {
            r[0]
            for r in get_db().execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    # Boot upgraded the old table in place and created the newer tables.
    assert {"timezone", "boroughs_json", "last_rollover_key", "show_everything"} <= cols
    assert {"app_config", "usage_counters"} <= tables
