# WIKIMAN
WIKI SOFTWARE THAT IS ACTUALLY USABLE

A small, self-hosted wiki. It runs on Flask and stores everything in one SQLite file. No external database, no build step, no JavaScript framework.

## Features

- **Markdown pages** with tables, fenced code, footnotes, definition lists, `~~strikethrough~~` and `[TOC]`.
- **Live split-pane editor**: preview updates as you type, Ctrl/⌘+S saves, and it warns before you lose unsaved changes.
- **Full revision history**: every save is kept. Compare any two revisions with a line diff, and revert in one click.
- **Edit-conflict detection**: if someone saves while you're editing, your text is kept and you're shown the diff instead of being silently overwritten.
- **`[[Wikilinks]]`** and `[[Target|label]]`. Links to missing pages are red and create the page when clicked.
- **Rename pages** with history intact. Optionally leave a redirect at the old address and rewrite `[[links]]` on every page that used the old name. Custom labels and code blocks are left alone, and redirect chains are fixed automatically. You can also write `#REDIRECT [[Page]]` by hand.
- **Backlinks** ("What links here"), **Wanted pages** (red links nobody has written yet) and **Orphaned pages**.
- **Full-text search** (SQLite FTS5) with stemming, prefix matching, ranked results and highlighted snippets. The top-bar search jumps straight to a page when the title matches exactly.
- **Accounts**: the first user to sign up becomes admin. Admins can protect pages (admin-only edits) and delete them. A deleted page keeps its history and gets it back if the page is recreated.
- Recent changes, per-user contributions, all pages A–Z, random page.
- Dark mode, mobile layout, CSRF protection, sanitised HTML output (`nh3`).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m wikiman            # http://127.0.0.1:5000
```

Open it, click **Sign up**, and the first account becomes the admin.

Serve it on your network with `python -m wikiman --host 0.0.0.0 --port 8080`. It uses `waitress`, a production WSGI server, when that's installed. Use `--debug` for auto-reload while hacking.

## Configuration

All settings are environment variables:

| Variable | Default | What it does |
|---|---|---|
| `WIKIMAN_DATABASE` | `instance/wikiman.db` | Path to the SQLite file |
| `WIKIMAN_SITE_NAME` | `WIKIMAN` | Name in the header and tab title |
| `WIKIMAN_ALLOW_REGISTRATION` | `1` | Set to `0` to close sign-ups. The first account can still be created. |
| `WIKIMAN_PRIVATE` | `0` | Set to `1` to require login to read anything |
| `WIKIMAN_SECRET_KEY` | auto | Session key. If unset, one is generated once and saved to `instance/secret_key`. |

## Admin commands

```bash
flask --app wikiman create-user alice --admin   # prompts for a password
flask --app wikiman set-admin bob               # or: --revoke
flask --app wikiman init-db                     # tables are also created on startup
```

## Backups

Everything lives in the database file. Copy it with SQLite's backup command so a write in progress can't corrupt the copy:

```bash
sqlite3 instance/wikiman.db ".backup wikiman-$(date +%F).db"
```

## Development

```bash
pip install pytest
python -m pytest
```

Layout:

```
wikiman/
  __init__.py   app factory, config, CSRF
  db.py         schema, connection, CLI commands
  store.py      pages, revisions, links, search queries
  markup.py     Markdown + wikilinks, sanitising, diffs, slugs
  auth.py       sign up, log in, permissions
  wiki.py       all wiki routes
  templates/    Jinja templates
  static/       style.css
tests/          pytest suite
```

## Licence

MIT
