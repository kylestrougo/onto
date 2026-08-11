#!/usr/bin/env bash
# Nightly SQLite backup, keeping the last 14 days.
#
# Uses SQLite's online backup API rather than `cp`: the database runs in WAL
# mode and is written to while this runs, so a plain file copy can capture a
# torn page and produce an archive that only fails when you need it.
#
# Driven through python3 rather than the sqlite3 CLI on purpose — the CLI is a
# separate Debian package that is not installed by default, and its absence
# would fail this job silently every night until someone read the log.
set -euo pipefail

DB="${ONTO_DB:-/home/io/onto/onto.db}"
DEST="${ONTO_BACKUP_DIR:-/home/io/onto/backups}"
KEEP_DAYS=14

[ -f "$DB" ] || { echo "$(date -Is) no database at $DB" >&2; exit 1; }

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/onto-$STAMP.db"

python3 - "$DB" "$OUT" <<'PY'
import sqlite3
import sys

src_path, out_path = sys.argv[1], sys.argv[2]
# Read-only source: a backup should never be the thing that writes to the DB.
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(out_path)
with dst:
    src.backup(dst)
dst.close()
src.close()
PY

gzip -9 "$OUT"

# Prune old archives.
find "$DEST" -name 'onto-*.db.gz' -mtime "+$KEEP_DAYS" -delete

echo "$(date -Is) backed up to $OUT.gz ($(du -h "$OUT.gz" | cut -f1))"
