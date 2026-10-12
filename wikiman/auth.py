import functools
import re
import sqlite3

from flask import (
    Blueprint, abort, current_app, flash, g, redirect, render_template, request,
    session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__, url_prefix="/auth")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def validate_credentials(username, password):
    if not USERNAME_RE.match(username or ""):
        return "Username must be 3–32 characters: letters, numbers, dot, dash or underscore."
    if len(password or "") < 8:
        return "Password must be at least 8 characters."
    return None


def create_user(username, password, is_admin=False):
    db = get_db()
    if not is_admin:
        # The first account on a fresh wiki owns it.
        is_admin = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    with db:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), 1 if is_admin else 0),
        )
    return cur.lastrowid


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("wiki.index")


@bp.before_app_request
def load_logged_in_user():
    uid = session.get("user_id")
    g.user = None
    if uid is not None:
        g.user = get_db().execute(
            "SELECT id, username, is_admin FROM users WHERE id = ?", (uid,)
        ).fetchone()
        if g.user is None:
            session.pop("user_id", None)
    if current_app.config["PRIVATE"] and g.user is None:
        if request.endpoint not in {"auth.login", "auth.register", "static"}:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            flash("Log in to do that.", "info")
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(**kwargs)

    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(**kwargs):
        if not g.user["is_admin"]:
            abort(403)
        return view(**kwargs)

    return wrapped


@bp.route("/register", methods=("GET", "POST"))
def register():
    db = get_db()
    first_user = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    if not current_app.config["ALLOW_REGISTRATION"] and not first_user:
        abort(403, "Registration is closed on this wiki.")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        error = validate_credentials(username, password)
        if not error and password != confirm:
            error = "Passwords don't match."
        if not error:
            try:
                uid = create_user(username, password)
            except sqlite3.IntegrityError:
                error = "That username is taken."
            else:
                session.clear()
                session["user_id"] = uid
                flash(
                    f"Welcome, {username}."
                    + (" You're the first user, so you're the admin." if first_user else ""),
                    "success",
                )
                return redirect(_safe_next(request.args.get("next")))
        flash(error, "error")
    return render_template("auth/register.html", first_user=first_user)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Wrong username or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = bool(request.form.get("remember"))
            return redirect(_safe_next(request.args.get("next")))
    return render_template("auth/login.html")


@bp.route("/logout", methods=("POST",))
def logout():
    session.clear()
    return redirect(url_for("wiki.index"))


@bp.route("/password", methods=("GET", "POST"))
@login_required
def change_password():
    if request.method == "POST":
        db = get_db()
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)).fetchone()
        new = request.form.get("new", "")
        if not check_password_hash(row["password_hash"], request.form.get("current", "")):
            flash("Current password is wrong.", "error")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new != request.form.get("confirm", ""):
            flash("Passwords don't match.", "error")
        else:
            with db:
                db.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new), g.user["id"]),
                )
            flash("Password changed.", "success")
            return redirect(url_for("wiki.user", username=g.user["username"]))
    return render_template("auth/password.html")
