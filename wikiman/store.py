"""Data access for pages, revisions, links and search."""
import re

from markupsafe import Markup, escape

from .db import get_db
from .markup import link_targets, redirect_target, render, rewrite_links, slugify


class RenameError(Exception):
    pass


class EditConflict(Exception):
    def __init__(self, current_rev):
        super().__init__("Page changed since you started editing.")
        self.current_rev = current_rev


def get_page(slug, include_deleted=False):
    sql = """
        SELECT p.*, r.body, r.summary, r.created_at AS rev_time, r.user_id,
               u.username AS editor
        FROM pages p
        LEFT JOIN revisions r ON r.id = p.current_rev_id
        LEFT JOIN users u ON u.id = r.user_id
        WHERE p.slug = ?
    """
    if not include_deleted:
        sql += " AND p.deleted = 0"
    return get_db().execute(sql, (slug,)).fetchone()


def existing_slugs(slugs):
    slugs = list(slugs)
    if not slugs:
        return set()
    marks = ",".join("?" * len(slugs))
    rows = get_db().execute(
        f"SELECT slug FROM pages WHERE deleted = 0 AND slug IN ({marks})", slugs
    ).fetchall()
    return {r["slug"] for r in rows}


def render_page(body):
    """Render with red/blue links resolved against the database."""
    exists = existing_slugs(link_targets(body))
    html, _ = render(body, resolver=lambda s: s in exists)
    return html


def save_page(slug, title, body, summary, user_id, base_rev_id=None):
    """Create or update a page. Returns the new revision id, or None if nothing changed.

    Raises EditConflict if base_rev_id is given and the page moved on since.
    """
    db = get_db()
    body = _normalise(body)
    title = title.strip()
    page = db.execute("SELECT * FROM pages WHERE slug = ?", (slug,)).fetchone()

    if page is not None and not page["deleted"]:
        current = db.execute(
            "SELECT * FROM revisions WHERE id = ?", (page["current_rev_id"],)
        ).fetchone()
        if base_rev_id is not None and current and int(base_rev_id) != current["id"]:
            raise EditConflict(current)
        if current and current["body"] == body and current["title"] == title:
            return None

    with db:
        if page is None:
            cur = db.execute("INSERT INTO pages (slug, title) VALUES (?, ?)", (slug, title))
            page_id = cur.lastrowid
        else:
            page_id = page["id"]
        rev_id = _write_revision(db, page_id, title, body, summary, user_id)
    return rev_id


def _normalise(body):
    return (body or "").replace("\r\n", "\n").rstrip() + "\n"


def _write_revision(db, page_id, title, body, summary, user_id):
    """Append a revision and make it current. Caller owns the transaction."""
    cur = db.execute(
        "INSERT INTO revisions (page_id, title, body, summary, user_id) VALUES (?, ?, ?, ?, ?)",
        (page_id, title, body, (summary or "").strip()[:300], user_id),
    )
    rev_id = cur.lastrowid
    db.execute(
        """UPDATE pages SET title = ?, current_rev_id = ?, deleted = 0,
           updated_at = datetime('now') WHERE id = ?""",
        (title, rev_id, page_id),
    )
    _reindex(db, page_id, title, body)
    return rev_id


def rename_page(old_slug, new_title, user_id, *, leave_redirect=True, update_links=False,
                is_admin=False):
    """Move a page to a new title/address.

    Returns {"slug": new_slug, "updated": [titles], "skipped": [titles]}.
    Raises RenameError with a human-readable message.
    """
    db = get_db()
    new_title = (new_title or "").strip()
    new_slug = slugify(new_title)
    if not new_slug:
        raise RenameError("The new title needs at least one letter or number.")
    page = get_page(old_slug)
    if page is None:
        raise RenameError("That page doesn't exist.")
    old_title = page["title"]
    summary = f"Renamed {old_title} → {new_title}"

    if new_slug == old_slug:
        if new_title == old_title:
            raise RenameError("That's already its title.")
        save_page(old_slug, new_title, page["body"], summary, user_id)
        return {"slug": old_slug, "updated": [], "skipped": []}

    clash = db.execute("SELECT id, deleted FROM pages WHERE slug = ?", (new_slug,)).fetchone()
    if clash and not clash["deleted"]:
        raise RenameError(f"A page called “{new_title}” already exists. "
                          "Rename or delete that one first.")

    updated, skipped = [], []
    with db:
        if clash:
            # A deleted page holds the address. Park it (history intact) out of the way.
            db.execute("UPDATE pages SET slug = slug || '--deleted-' || id WHERE id = ?",
                       (clash["id"],))

        body = page["body"]
        if update_links:
            body = rewrite_links(body, old_slug, new_title)
        db.execute("UPDATE pages SET slug = ? WHERE id = ?", (new_slug, page["id"]))
        _write_revision(db, page["id"], new_title, body, summary, user_id)

        if leave_redirect:
            cur = db.execute("INSERT INTO pages (slug, title) VALUES (?, ?)",
                             (old_slug, old_title))
            _write_revision(db, cur.lastrowid, old_title, f"#REDIRECT [[{new_title}]]\n",
                            f"Redirect to {new_title} after rename", user_id)

        # Every page that linked to the old address.
        referrers = db.execute(
            """SELECT p.id, p.slug, p.title, p.protected, r.body FROM links l
               JOIN pages p ON p.id = l.src_page_id
               JOIN revisions r ON r.id = p.current_rev_id
               WHERE l.dst_slug = ? AND p.deleted = 0 AND p.id != ? AND p.slug != ?""",
            (old_slug, page["id"], old_slug),
        ).fetchall()
        for ref in referrers:
            is_redirect = redirect_target(ref["body"]) == old_slug
            if not (is_redirect or update_links):
                continue
            if ref["protected"] and not is_admin:
                skipped.append(ref["title"])
                continue
            if is_redirect:
                # Fix what would otherwise become a double redirect.
                new_body = f"#REDIRECT [[{new_title}]]\n"
            else:
                new_body = rewrite_links(ref["body"], old_slug, new_title)
            if new_body != ref["body"]:
                _write_revision(db, ref["id"], ref["title"], new_body,
                                f"Updated links: {old_title} → {new_title}",
                                user_id)
                if not is_redirect:
                    updated.append(ref["title"])
    return {"slug": new_slug, "updated": updated, "skipped": skipped}


def _reindex(db, page_id, title, body):
    db.execute("DELETE FROM links WHERE src_page_id = ?", (page_id,))
    db.executemany(
        "INSERT OR IGNORE INTO links (src_page_id, dst_slug) VALUES (?, ?)",
        [(page_id, s) for s in link_targets(body)],
    )
    db.execute("DELETE FROM pages_fts WHERE rowid = ?", (page_id,))
    db.execute(
        "INSERT INTO pages_fts (rowid, title, body) VALUES (?, ?, ?)",
        (page_id, title, "" if redirect_target(body) else body),
    )


def delete_page(slug):
    db = get_db()
    page = db.execute("SELECT id FROM pages WHERE slug = ?", (slug,)).fetchone()
    if not page:
        return False
    with db:
        db.execute("UPDATE pages SET deleted = 1 WHERE id = ?", (page["id"],))
        db.execute("DELETE FROM links WHERE src_page_id = ?", (page["id"],))
        db.execute("DELETE FROM pages_fts WHERE rowid = ?", (page["id"],))
    return True


def set_protected(slug, protected):
    db = get_db()
    with db:
        db.execute("UPDATE pages SET protected = ? WHERE slug = ?", (1 if protected else 0, slug))


def history(page_id):
    return get_db().execute(
        """SELECT r.id, r.title, r.summary, r.created_at, r.user_id, u.username,
                  length(r.body) AS size
           FROM revisions r LEFT JOIN users u ON u.id = r.user_id
           WHERE r.page_id = ? ORDER BY r.id DESC""",
        (page_id,),
    ).fetchall()


def get_revision(page_id, rev_id):
    return get_db().execute(
        """SELECT r.*, u.username FROM revisions r LEFT JOIN users u ON u.id = r.user_id
           WHERE r.page_id = ? AND r.id = ?""",
        (page_id, rev_id),
    ).fetchone()


def previous_revision(page_id, rev_id):
    return get_db().execute(
        "SELECT * FROM revisions WHERE page_id = ? AND id < ? ORDER BY id DESC LIMIT 1",
        (page_id, rev_id),
    ).fetchone()


def _direct_backlinks(slug):
    return get_db().execute(
        """SELECT p.slug, p.title, ltrim(r.body) LIKE '#REDIRECT%' AS is_redirect
           FROM links l JOIN pages p ON p.id = l.src_page_id
           JOIN revisions r ON r.id = p.current_rev_id
           WHERE l.dst_slug = ? AND p.deleted = 0 AND p.slug != ?
           ORDER BY p.title COLLATE NOCASE""",
        (slug, slug),
    ).fetchall()


def backlinks(slug):
    """Pages linking here, including ones that link through a redirect."""
    out, seen = [], {slug}
    for row in _direct_backlinks(slug):
        if row["slug"] in seen:
            continue
        seen.add(row["slug"])
        out.append({"slug": row["slug"], "title": row["title"],
                    "is_redirect": bool(row["is_redirect"]), "via": None})
        if row["is_redirect"]:
            for sub in _direct_backlinks(row["slug"]):
                if sub["slug"] not in seen:
                    seen.add(sub["slug"])
                    out.append({"slug": sub["slug"], "title": sub["title"],
                                "is_redirect": bool(sub["is_redirect"]), "via": row["title"]})
    return out


def all_pages():
    return get_db().execute(
        """SELECT p.slug, p.title, p.updated_at, ltrim(r.body) LIKE '#REDIRECT%' AS is_redirect
           FROM pages p JOIN revisions r ON r.id = p.current_rev_id WHERE p.deleted = 0
           ORDER BY p.title COLLATE NOCASE"""
    ).fetchall()


def recent_changes(limit=100, user_id=None):
    sql = """SELECT r.id, r.summary, r.created_at, u.username, p.slug, p.title, p.deleted,
                    length(r.body) AS size,
                    (SELECT length(body) FROM revisions r2 WHERE r2.page_id = r.page_id
                       AND r2.id < r.id ORDER BY r2.id DESC LIMIT 1) AS prev_size
             FROM revisions r JOIN pages p ON p.id = r.page_id
             LEFT JOIN users u ON u.id = r.user_id"""
    args = []
    if user_id is not None:
        sql += " WHERE r.user_id = ?"
        args.append(user_id)
    sql += " ORDER BY r.id DESC LIMIT ?"
    args.append(limit)
    return get_db().execute(sql, args).fetchall()


def wanted_pages():
    return get_db().execute(
        """SELECT l.dst_slug AS slug, COUNT(*) AS refs FROM links l
           JOIN pages src ON src.id = l.src_page_id AND src.deleted = 0
           LEFT JOIN pages p ON p.slug = l.dst_slug AND p.deleted = 0
           WHERE p.id IS NULL GROUP BY l.dst_slug ORDER BY refs DESC, l.dst_slug"""
    ).fetchall()


def orphaned_pages(main_slug):
    return get_db().execute(
        """SELECT p.slug, p.title FROM pages p JOIN revisions r ON r.id = p.current_rev_id
           WHERE p.deleted = 0 AND p.slug != ? AND ltrim(r.body) NOT LIKE '#REDIRECT%'
           AND NOT EXISTS (SELECT 1 FROM links l JOIN pages s ON s.id = l.src_page_id
                           WHERE l.dst_slug = p.slug AND s.deleted = 0 AND s.id != p.id)
           ORDER BY p.title COLLATE NOCASE""",
        (main_slug,),
    ).fetchall()


def random_slug():
    row = get_db().execute(
        "SELECT slug FROM pages WHERE deleted = 0 ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    return row["slug"] if row else None


_TERM_RE = re.compile(r"\w+", re.UNICODE)


def search(query, limit=50):
    terms = _TERM_RE.findall(query or "")
    if not terms:
        return []
    fts_query = " ".join(f'"{t}"*' for t in terms[:12])
    rows = get_db().execute(
        """SELECT p.slug, p.title, p.updated_at,
                  snippet(pages_fts, 1, char(2), char(3), ' … ', 16) AS snip
           FROM pages_fts JOIN pages p ON p.id = pages_fts.rowid
           WHERE pages_fts MATCH ? AND p.deleted = 0
           ORDER BY bm25(pages_fts, 8.0, 1.0) LIMIT ?""",
        (fts_query, limit),
    ).fetchall()
    out = []
    for r in rows:
        snip = str(escape(r["snip"] or ""))
        snip = snip.replace("\x02", "<mark>").replace("\x03", "</mark>")
        out.append({"slug": r["slug"], "title": r["title"], "updated_at": r["updated_at"],
                    "snippet": Markup(snip)})
    return out


def title_match(query):
    """Exact title match, for 'go straight to the page' search behaviour."""
    from .markup import slugify

    slug = slugify(query)
    if not slug:
        return None
    row = get_db().execute(
        "SELECT slug FROM pages WHERE slug = ? AND deleted = 0", (slug,)
    ).fetchone()
    return row["slug"] if row else None


def get_user(username):
    return get_db().execute(
        "SELECT id, username, is_admin, created_at FROM users WHERE username = ?", (username,)
    ).fetchone()
