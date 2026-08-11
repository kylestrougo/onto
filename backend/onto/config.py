"""Configuration, read from the environment.

Nothing secret is ever hard-coded or committed; see .env.example for the shape.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    """Load .env, letting it beat anything already in the environment.

    override=True is deliberate. Without it an unrelated export in the operator's
    shell silently shadows the file they actually edited — and because systemd
    passes the same file in as EnvironmentFile, the result is a service that
    works while `flask ...` in a terminal fails against the same config, which is
    a genuinely baffling thing to debug. A key exported for some other tool is
    far more likely to be stale than the file deployed next to the app, so the
    file wins.
    """
    load_dotenv(path, override=True)


_load_env(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # ── Core ────────────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get("ONTO_SECRET_KEY", "")
    DATABASE = os.environ.get("ONTO_DB", str(BASE_DIR / "onto.db"))
    PUBLIC_URL = os.environ.get("ONTO_PUBLIC_URL", "http://localhost:5000").rstrip("/")
    # Where completion photos land. Kept out of static/ so a backup of the DB
    # directory picks them up together.
    UPLOAD_DIR = os.environ.get("ONTO_UPLOAD_DIR", str(BASE_DIR / "uploads"))

    # ── Sessions ────────────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("ONTO_COOKIE_SECURE", True)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30  # 30 days

    # ── OpenRouter ──────────────────────────────────────────────────────
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE = os.environ.get(
        "OPENROUTER_BASE", "https://openrouter.ai/api/v1"
    ).rstrip("/")
    OPENROUTER_TIMEOUT = _int("OPENROUTER_TIMEOUT", 45)
    # Hard ceiling on one generation across the WHOLE fallback chain, so a
    # sequence of timing-out models can't pin a waitress thread for minutes.
    GENERATION_BUDGET = _int("ONTO_GENERATION_BUDGET", 60)

    # Fallback chain used until an admin saves one. Free models churn — these
    # are a starting point to be re-picked from the admin page, not gospel.
    DEFAULT_MODEL_CHAIN = [
        m.strip()
        for m in os.environ.get(
            "ONTO_MODEL_CHAIN",
            "meta-llama/llama-3.3-70b-instruct:free,"
            "google/gemma-2-9b-it:free,"
            "mistralai/mistral-7b-instruct:free",
        ).split(",")
        if m.strip()
    ]

    # ── Abuse guards ────────────────────────────────────────────────────
    SIGNUP_CAP_IP = _int("ONTO_SIGNUP_CAP_IP", 5)
    DAILY_CAP_USER = _int("ONTO_DAILY_CAP_USER", 300)

    # ── Email (SMTP app password; dry-run prints to the log) ────────────
    SMTP_HOST = os.environ.get("ONTO_SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = _int("ONTO_SMTP_PORT", 587)
    SMTP_USER = os.environ.get("ONTO_SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("ONTO_SMTP_PASSWORD", "")
    MAIL_FROM = os.environ.get("ONTO_MAIL_FROM", "") or os.environ.get("ONTO_SMTP_USER", "")
    MAIL_FROM_NAME = os.environ.get("ONTO_MAIL_FROM_NAME", "Onto")
    MAIL_DRY_RUN = _bool("ONTO_MAIL_DRY_RUN", False)
    # Fallback timezone for rows saved before the browser told us one.
    DEFAULT_TZ = os.environ.get("ONTO_DEFAULT_TZ", "America/New_York")

    # ── Discovery ───────────────────────────────────────────────────────
    SEARXNG_URL = os.environ.get("ONTO_SEARXNG_URL", "").rstrip("/")
    # City the shared event corpus covers. One corpus, all users (spec 8.3).
    CITY = os.environ.get("ONTO_CITY", "NYC")

    # The single admin. First account created with this username is promoted.
    ADMIN_USERNAME = os.environ.get("ONTO_ADMIN_USERNAME", "")

    # New accounts start with an empty list; the library page offers the
    # starter goals as one-tap quick-adds instead. Flip this on to go back to
    # pre-seeding every new account.
    STARTER_GOALS_ON_SIGNUP = _bool("ONTO_STARTER_GOALS_ON_SIGNUP", False)
