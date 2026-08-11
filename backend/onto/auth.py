"""Accounts. Deliberately light — this is a self-hosted app for friends.

Open self-signup, username + password, argon2 hashes, Flask-Login session
cookies, per-IP rate limiting on signup. Server-rendered forms, not a JSON
API: this app is Jinja-first.
"""
from __future__ import annotations

import re
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)

from .db import execute, query
from .ratelimit import check_signup_rate

bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Sign in to do that."

# Low-memory argon2 parameters — a Pi with 1GB shared between several
# services. Still far above the bar for a personal app.
_hasher = PasswordHasher(time_cost=2, memory_cost=32 * 1024, parallelism=1)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,30}$")
MIN_PASSWORD = 10
BAD_CREDENTIALS = "Username or password didn't match."


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.role = row["role"]
        self.timezone = row["timezone"]
        self.show_everything = bool(row["show_everything"])

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id: str):
    row = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    return User(row) if row else None


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def init_app(app) -> None:
    login_manager.init_app(app)
    app.register_blueprint(bp)


def _capture_timezone(user_id: int) -> None:
    """Store the browser-reported IANA timezone if the form carried one.

    A hidden input filled by JS; absent or garbage input leaves the stored
    value alone, so a curl login can't blank a good timezone.
    """
    tz = (request.form.get("timezone") or "").strip()
    if tz and len(tz) <= 64 and re.fullmatch(r"[A-Za-z0-9_+/-]+", tz):
        execute("UPDATE users SET timezone = ? WHERE id = ?", (tz, user_id))


@bp.get("/signup")
def signup_form():
    if current_user.is_authenticated:
        return redirect(url_for("week.home"))
    return render_template("signup.html")


@bp.post("/signup")
def signup():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    error = None
    if not USERNAME_RE.match(username):
        error = "Usernames are 3-30 letters, numbers, dots, dashes or underscores."
    elif len(password) < MIN_PASSWORD:
        error = f"Use at least {MIN_PASSWORD} characters."
    if error is None:
        allowed, msg = check_signup_rate()
        if not allowed:
            error = msg
    if error is None and query(
        "SELECT id FROM users WHERE username = ?", (username,), one=True
    ):
        error = "That username is taken."
    if error:
        return render_template("signup.html", error=error, username=username), 400

    # The configured admin username gets the single admin role on first signup.
    admin_username = (current_app.config.get("ADMIN_USERNAME") or "").strip().lower()
    already_admin = query("SELECT id FROM users WHERE role = 'admin'", (), one=True)
    role = (
        "admin"
        if (admin_username and username.lower() == admin_username and not already_admin)
        else "user"
    )

    user_id = execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, _hasher.hash(password), role),
    )
    _capture_timezone(user_id)
    _on_signup(user_id)

    row = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    login_user(User(row), remember=True)
    return redirect(url_for("week.home"))


def _on_signup(user_id: int) -> None:
    """Post-signup hooks. Accounts start empty by default — the library page
    offers the starter goals as quick-adds instead of pre-filling the list."""
    if current_app.config.get("STARTER_GOALS_ON_SIGNUP"):
        from . import seed

        seed.starter_goals_for(user_id)


@bp.get("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("week.home"))
    return render_template("login.html")


@bp.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    row = query("SELECT * FROM users WHERE username = ?", (username,), one=True)
    # Same response whether the username is unknown or the password is wrong.
    if not row:
        return render_template("login.html", error=BAD_CREDENTIALS, username=username), 401
    try:
        _hasher.verify(row["password_hash"], password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return render_template("login.html", error=BAD_CREDENTIALS, username=username), 401

    if _hasher.check_needs_rehash(row["password_hash"]):
        execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (_hasher.hash(password), row["id"]),
        )

    _capture_timezone(row["id"])
    login_user(User(row), remember=True)
    dest = request.form.get("next") or ""
    # Only ever redirect within the app.
    if not dest.startswith("/") or dest.startswith("//"):
        dest = url_for("week.home")
    return redirect(dest)


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.")
    return redirect(url_for("auth.login"))
