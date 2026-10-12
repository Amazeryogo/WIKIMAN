from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
    request, url_for,
)

from . import store
from .auth import admin_required, login_required
from .markup import diff_lines, diff_stats, redirect_target, slugify

bp = Blueprint("wiki", __name__)


def _can_edit(page):
    if g.user is None:
        return False
    if page is not None and page["protected"] and not g.user["is_admin"]:
        return False
    return True


def _page_or_404(slug):
    page = store.get_page(slug)
    if page is None:
        abort(404, f"No page called '{slug}'.")
    return page


@bp.app_context_processor
def _helpers():
    return {"can_edit": _can_edit}


@bp.route("/")
def index():
    return redirect(url_for("wiki.view", slug=current_app.config["MAIN_PAGE"]))


@bp.route("/wiki/<slug>")
def view(slug):
    canonical = slugify(slug)
    if canonical and canonical != slug:
        return redirect(url_for("wiki.view", slug=canonical), 301)
    page = store.get_page(slug)
    if page is None:
        deleted = store.get_page(slug, include_deleted=True)
        return render_template(
            "missing.html",
            slug=slug,
            title=request.args.get("title") or slug.replace("-", " ").capitalize(),
            deleted=deleted,
            backlinks=store.backlinks(slug),
        ), 404
    target = redirect_target(page["body"])
    if target and request.args.get("redirect") != "no":
        # Follow the chain (max 5 hops, no loops) and land on the real page.
        seen, final = {slug}, None
        while target and target not in seen and len(seen) <= 5:
            nxt = store.get_page(target)
            if nxt is None:
                break
            final = target
            seen.add(target)
            target = redirect_target(nxt["body"])
        if final and not redirect_target(store.get_page(final)["body"]):
            return redirect(url_for("wiki.view", slug=final, rf=slug))
    redirected_from = None
    if request.args.get("rf"):
        redirected_from = store.get_page(slugify(request.args["rf"]))
    return render_template(
        "page.html",
        page=page,
        html=None if target else store.render_page(page["body"]),
        redirect_to=target,
        redirect_exists=bool(target and store.existing_slugs([target])),
        redirected_from=redirected_from,
        backlinks=store.backlinks(slug),
    )


@bp.route("/wiki/<slug>/edit", methods=("GET", "POST"))
@login_required
def edit(slug):
    canonical = slugify(slug)
    if not canonical:
        abort(404)
    if canonical != slug:
        return redirect(url_for("wiki.edit", slug=canonical, **request.args), 301)

    page = store.get_page(slug)
    if not _can_edit(page):
        abort(403, "This page is protected. Only admins can edit it.")

    title = page["title"] if page else (
        request.args.get("title") or slug.replace("-", " ").capitalize()
    )
    body = page["body"] if page else ""
    base_rev = page["current_rev_id"] if page else ""
    summary = ""
    conflict = None

    if request.method == "POST":
        title = request.form.get("title", "").strip() or title
        body = request.form.get("body", "")
        summary = request.form.get("summary", "")
        base_rev = request.form.get("base_rev") or None

        if slugify(title) != slug:
            flash("The title has to match the page address. Only capitalisation and "
                  "punctuation can change here.", "error")
        elif "preview" in request.form:
            pass
        else:
            try:
                rev = store.save_page(slug, title, body, summary, g.user["id"],
                                      base_rev_id=base_rev if page else None)
            except store.EditConflict as exc:
                conflict = diff_lines(exc.current_rev["body"], body)
                base_rev = exc.current_rev["id"]
                flash("Someone else saved this page while you were editing. Your text is "
                      "still below; the diff shows how it differs from their version. "
                      "Merge by hand, then save again.", "error")
            else:
                if rev is None:
                    flash("No changes to save.", "info")
                else:
                    flash("Saved.", "success")
                return redirect(url_for("wiki.view", slug=slug))

    preview = store.render_page(body) \
        if request.method == "POST" and "preview" in request.form else None
    return render_template(
        "edit.html", slug=slug, page=page, title=title, body=body, summary=summary,
        base_rev=base_rev, preview=preview, conflict=conflict,
    )


@bp.route("/wiki/<slug>/rename", methods=("GET", "POST"))
@login_required
def rename(slug):
    page = _page_or_404(slug)
    if not _can_edit(page):
        abort(403, "This page is protected. Only admins can rename it.")
    if slug == current_app.config["MAIN_PAGE"]:
        abort(403, "The main page can't be renamed. Its title can still be changed from Edit.")
    backlinks = [b for b in store.backlinks(slug) if not b["via"]]
    new_title = page["title"]
    leave_redirect, update_links = True, True
    if request.method == "POST":
        new_title = request.form.get("new_title", "")
        leave_redirect = bool(request.form.get("leave_redirect"))
        update_links = bool(request.form.get("update_links"))
        try:
            result = store.rename_page(
                slug, new_title, g.user["id"], leave_redirect=leave_redirect,
                update_links=update_links, is_admin=bool(g.user["is_admin"]),
            )
        except store.RenameError as exc:
            flash(str(exc), "error")
        else:
            msg = f"Renamed to “{new_title.strip()}”."
            if result["updated"]:
                n = len(result["updated"])
                msg += f" Updated links on {n} page{'s' if n != 1 else ''}."
            if leave_redirect and result["slug"] != slug:
                msg += " The old address redirects here."
            flash(msg, "success")
            if result["skipped"]:
                flash("Protected pages that still use the old name (only admins can edit "
                      "them): " + ", ".join(result["skipped"]), "info")
            return redirect(url_for("wiki.view", slug=result["slug"]))
    return render_template(
        "rename.html", page=page, new_title=new_title, backlinks=backlinks,
        leave_redirect=leave_redirect, update_links=update_links,
    )


@bp.route("/api/preview", methods=("POST",))
@login_required
def api_preview():
    return jsonify(html=str(store.render_page(request.form.get("body", ""))))


@bp.route("/wiki/<slug>/history")
def page_history(slug):
    page = _page_or_404(slug)
    revs = store.history(page["id"])
    rows = []
    for i, r in enumerate(revs):
        prev_size = revs[i + 1]["size"] if i + 1 < len(revs) else 0
        rows.append({**dict(r), "delta": r["size"] - prev_size})
    return render_template("history.html", page=page, revisions=rows)


@bp.route("/wiki/<slug>/rev/<int:rev_id>")
def revision(slug, rev_id):
    page = _page_or_404(slug)
    rev = store.get_revision(page["id"], rev_id)
    if rev is None:
        abort(404, "No such revision.")
    return render_template(
        "revision.html", page=page, rev=rev, html=store.render_page(rev["body"]),
        is_current=rev["id"] == page["current_rev_id"],
    )


@bp.route("/wiki/<slug>/diff")
def diff(slug):
    page = _page_or_404(slug)
    to_id = request.args.get("to", type=int) or page["current_rev_id"]
    new = store.get_revision(page["id"], to_id)
    if new is None:
        abort(404, "No such revision.")
    from_id = request.args.get("from", type=int)
    old = store.get_revision(page["id"], from_id) if from_id else \
        store.previous_revision(page["id"], new["id"])
    if old is not None and old["id"] > new["id"]:
        old, new = new, old
    old_body = old["body"] if old else ""
    added, removed = diff_stats(old_body, new["body"])
    return render_template(
        "diff.html", page=page, old=old, new=new,
        rows=diff_lines(old_body, new["body"]), added=added, removed=removed,
        title_changed=old is not None and old["title"] != new["title"],
    )


@bp.route("/wiki/<slug>/revert/<int:rev_id>", methods=("POST",))
@login_required
def revert(slug, rev_id):
    page = _page_or_404(slug)
    if not _can_edit(page):
        abort(403)
    rev = store.get_revision(page["id"], rev_id)
    if rev is None:
        abort(404)
    result = store.save_page(slug, rev["title"], rev["body"],
                             f"Reverted to revision {rev_id}", g.user["id"])
    flash("Reverted." if result else "That revision is already the current one.",
          "success" if result else "info")
    return redirect(url_for("wiki.view", slug=slug))


@bp.route("/wiki/<slug>/delete", methods=("POST",))
@admin_required
def delete(slug):
    _page_or_404(slug)
    if slug == current_app.config["MAIN_PAGE"]:
        flash("The main page can't be deleted.", "error")
        return redirect(url_for("wiki.view", slug=slug))
    store.delete_page(slug)
    flash("Page deleted. Its history is kept; recreating it brings the history back.", "success")
    return redirect(url_for("wiki.index"))


@bp.route("/wiki/<slug>/protect", methods=("POST",))
@admin_required
def protect(slug):
    page = _page_or_404(slug)
    store.set_protected(slug, not page["protected"])
    flash("Page unprotected." if page["protected"] else
          "Page protected. Only admins can edit it now.", "success")
    return redirect(url_for("wiki.view", slug=slug))


@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if q and request.args.get("go"):
        hit = store.title_match(q)
        if hit:
            return redirect(url_for("wiki.view", slug=hit))
    results = store.search(q) if q else []
    slug = slugify(q)
    return render_template(
        "search.html", q=q, results=results, create_slug=slug,
        exact=bool(slug and store.existing_slugs([slug])),
    )


@bp.route("/special/all")
def all_pages():
    pages = store.all_pages()
    groups = {}
    for p in pages:
        key = p["title"][:1].upper()
        if not key.isalpha():
            key = "#"
        groups.setdefault(key, []).append(p)
    return render_template("special/all.html", groups=groups, count=len(pages))


@bp.route("/special/recent")
def recent():
    return render_template("special/recent.html", changes=store.recent_changes(150))


@bp.route("/special/wanted")
def wanted():
    return render_template("special/wanted.html", wanted=store.wanted_pages())


@bp.route("/special/orphans")
def orphans():
    return render_template(
        "special/orphans.html",
        pages=store.orphaned_pages(current_app.config["MAIN_PAGE"]),
    )


@bp.route("/special/random")
def random_page():
    slug = store.random_slug()
    return redirect(url_for("wiki.view", slug=slug) if slug else url_for("wiki.index"))


@bp.route("/user/<username>")
def user(username):
    u = store.get_user(username)
    if u is None:
        abort(404, "No such user.")
    return render_template("user.html", u=u, changes=store.recent_changes(200, user_id=u["id"]))
