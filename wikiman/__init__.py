"""WIKIMAN — wiki software that is actually usable."""
import os
import secrets

from flask import Flask, abort, g, render_template, request, session

__version__ = "0.1.0"


def _env_bool(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _load_secret_key(instance_path):
    key = os.environ.get("WIKIMAN_SECRET_KEY")
    if key:
        return key
    path = os.path.join(instance_path, "secret_key")
    if os.path.exists(path):
        with open(path) as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as fh:
        fh.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(
        DATABASE=os.environ.get(
            "WIKIMAN_DATABASE", os.path.join(app.instance_path, "wikiman.db")
        ),
        SITE_NAME=os.environ.get("WIKIMAN_SITE_NAME", "WIKIMAN"),
        ALLOW_REGISTRATION=_env_bool("WIKIMAN_ALLOW_REGISTRATION", True),
        PRIVATE=_env_bool("WIKIMAN_PRIVATE", False),
        MAIN_PAGE="main-page",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_secret_key(app.instance_path)

    from . import auth, db, wiki
    from .markup import format_time

    db.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(wiki.bp)

    app.jinja_env.filters["ago"] = format_time

    # --- CSRF -------------------------------------------------------------
    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        if app.config.get("TESTING") and app.config.get("CSRF_DISABLED"):
            return
        if request.method == "POST":
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not sent or not secrets.compare_digest(sent, session.get("csrf_token", "")):
                abort(400, "Invalid or missing CSRF token. Reload the page and try again.")

    @app.context_processor
    def inject_globals():
        return {"site_name": app.config["SITE_NAME"], "config": app.config}

    # --- Errors -----------------------------------------------------------
    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def http_error(err):
        return render_template("error.html", error=err), err.code

    with app.app_context():
        db.init_db()

    return app
