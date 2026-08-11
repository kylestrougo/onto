"""Onto — application factory.

A server-rendered Flask app: Jinja templates + htmx, no build step, no second
web server. Waitress serves it on the Pi; Tailscale Funnel terminates TLS.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from flask import Flask, render_template

from .config import Config


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    _configure_secret_key(app)
    logging.basicConfig(
        level=os.environ.get("ONTO_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from . import auth, cli, db, disclosure
    from .views import admin, library, mix as mix_view, month, social_views, week

    db.init_app(app)
    cli.init_app(app)
    auth.init_app(app)
    disclosure.init_app(app)
    app.register_blueprint(week.bp)
    app.register_blueprint(library.bp)
    app.register_blueprint(month.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(mix_view.bp)
    app.register_blueprint(social_views.bp)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/uploads/<path:name>")
    def uploaded(name: str):
        from flask import send_from_directory
        from flask_login import current_user

        # Photos ride the honor system like everything else, but never leave
        # the circle: only signed-in users can fetch them.
        if not current_user.is_authenticated:
            return render_template("error.html", code=403, message="You can't do that."), 403
        return send_from_directory(app.config["UPLOAD_DIR"], name)

    @app.errorhandler(404)
    def _not_found(_e):
        return render_template("error.html", code=404, message="That page doesn't exist."), 404

    @app.errorhandler(403)
    def _forbidden(_e):
        return render_template("error.html", code=403, message="You can't do that."), 403

    @app.errorhandler(500)
    def _server_error(_e):
        app.logger.exception("unhandled error")
        return render_template("error.html", code=500, message="Something broke on our end."), 500

    # Create tables on boot so a fresh Pi deploy is one command.
    with app.app_context():
        db.init_db()
        from . import seed

        seed.taxonomy()

    return app


def _configure_secret_key(app: Flask) -> None:
    """A stable secret key, without ever committing one.

    If ONTO_SECRET_KEY isn't set we generate one and persist it next to the
    database — otherwise every restart would silently log everyone out.
    """
    if app.config.get("SECRET_KEY"):
        return
    key_path = Path(app.config["DATABASE"]).with_suffix(".secret")
    if key_path.exists():
        app.config["SECRET_KEY"] = key_path.read_text().strip()
        return
    key = secrets.token_urlsafe(48)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key)
    key_path.chmod(0o600)
    app.config["SECRET_KEY"] = key
    app.logger.warning(
        "Generated a new SECRET_KEY at %s — set ONTO_SECRET_KEY to pin it.", key_path
    )
