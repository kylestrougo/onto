"""Completion photos (spec 6.3) — optional, honor-system flavour.

Files land in UPLOAD_DIR (next to the DB so backups travel together), named
by uuid, capped in size, and only ever served to signed-in users.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from flask import current_app

ALLOWED_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_BYTES = 5 * 1024 * 1024


def save(file) -> str | None:
    """Validate and store an uploaded image. Returns the stored filename or
    None when the file isn't acceptable (wrong type, too big, empty)."""
    name = (file.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_EXT:
        return None
    data = file.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        return None
    out_dir = Path(current_app.config["UPLOAD_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    (out_dir / filename).write_bytes(data)
    return filename
