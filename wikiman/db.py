import sqlite3

import click
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slug           TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    current_rev_id INTEGER REFERENCES revisions(id),
    protected      INTEGER NOT NULL DEFAULT 0,
    deleted        INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS revisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id    INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    summary    TEXT NOT NULL DEFAULT '',
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_revisions_page ON revisions(page_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_revisions_user ON revisions(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_revisions_time ON revisions(id DESC);

CREATE TABLE IF NOT EXISTS links (
    src_page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    dst_slug    TEXT NOT NULL,
    PRIMARY KEY (src_page_id, dst_slug)
);
CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst_slug);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    title, body, tokenize = 'porter unicode61'
);
"""

WELCOME = """Welcome to **WIKIMAN**, a wiki that is actually usable.

## Getting started

- Click **Edit** to change this page. Pages are written in [Markdown](https://commonmark.org/help/).
- Link to other pages with double brackets: `[[Another Page]]` or `[[Another Page|custom text]]`.
  Links to pages that don't exist yet show up in red, like [[Ideas]]. Click one to create it.
- Every edit is kept. Use **History** to compare revisions and roll back.
- Search covers titles and full text.

## Formatting cheatsheet

| You type | You get |
|---|---|
| `**bold**` | **bold** |
| `*italic*` | *italic* |
| `~~struck~~` | ~~struck~~ |
| `` `code` `` | `code` |
| `# Heading` | a heading |
| `- item` | a bullet list |

Add `[TOC]` on its own line to insert a table of contents.
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()
    if db.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0:
        from .store import save_page

        save_page(
            current_app.config["MAIN_PAGE"],
            "Main Page",
            WELCOME,
            "Created the wiki",
            None,
        )


@click.command("init-db")
def init_db_command():
    """Create tables (safe to run repeatedly)."""
    init_db()
    click.echo("Database ready at " + current_app.config["DATABASE"])


@click.command("create-user")
@click.argument("username")
@click.option("--admin", is_flag=True, help="Give the user admin rights.")
@click.password_option()
def create_user_command(username, admin, password):
    """Create a user from the command line."""
    from .auth import create_user, validate_credentials

    err = validate_credentials(username, password)
    if err:
        raise click.ClickException(err)
    try:
        create_user(username, password, is_admin=admin)
    except sqlite3.IntegrityError:
        raise click.ClickException(f"User '{username}' already exists.")
    click.echo(f"Created {'admin ' if admin else ''}user '{username}'.")


@click.command("set-admin")
@click.argument("username")
@click.option("--revoke", is_flag=True)
def set_admin_command(username, revoke):
    """Grant (or revoke) admin rights."""
    db = get_db()
    cur = db.execute(
        "UPDATE users SET is_admin = ? WHERE username = ?", (0 if revoke else 1, username)
    )
    db.commit()
    if not cur.rowcount:
        raise click.ClickException(f"No user named '{username}'.")
    click.echo(f"{'Revoked' if revoke else 'Granted'} admin for '{username}'.")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_user_command)
    app.cli.add_command(set_admin_command)
