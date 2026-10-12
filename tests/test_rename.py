from conftest import edit
from wikiman.markup import redirect_target, rewrite_links


def rename(client, slug, new_title, redirect=True, links=True):
    data = {"new_title": new_title}
    if redirect:
        data["leave_redirect"] = "1"
    if links:
        data["update_links"] = "1"
    return client.post(f"/wiki/{slug}/rename", data=data)


def test_rewrite_links_helper():
    body = "See [[Old Name]], [[old name|custom]], [[Other]].\n\n`[[Old Name]]`\n\n```\n[[Old Name]]\n```\n"
    out = rewrite_links(body, "old-name", "New Name")
    assert "[[New Name]], [[New Name|custom]], [[Other]]" in out
    assert "`[[Old Name]]`" in out and "```\n[[Old Name]]\n```" in out


def test_redirect_target_helper():
    assert redirect_target("#REDIRECT [[Foo Bar]]\n") == "foo-bar"
    assert redirect_target("  #redirect[[Foo|x]]") == "foo"
    assert redirect_target("text #REDIRECT [[Foo]]") is None


def test_rename_with_redirect_and_link_update(client, auth):
    auth.register()
    edit(client, "old-name", "the content", title="Old Name", summary="v1")
    edit(client, "old-name", "the content v2", title="Old Name", summary="v2")
    edit(client, "linker", "Go to [[Old Name]] or [[old name|here]].", title="Linker")

    r = rename(client, "old-name", "New Name")
    assert r.status_code == 302 and r.headers["Location"].endswith("/wiki/new-name")

    page = client.get("/wiki/new-name").data.decode()
    assert "the content v2" in page and "Linker" in page  # backlinks

    # history moved with the page (2 edits + rename)
    assert client.get("/wiki/new-name/history").data.decode().count("/wiki/new-name/rev/") == 3

    # old address redirects, with a 'redirected from' note
    r = client.get("/wiki/old-name")
    assert r.status_code == 302 and "/wiki/new-name?rf=old-name" in r.headers["Location"]
    assert "Redirected from" in client.get(r.headers["Location"]).data.decode()
    assert "is a redirect to" in client.get("/wiki/old-name?redirect=no").data.decode()

    # links on other pages rewritten
    linker = client.get("/wiki/linker/edit").data.decode()
    assert "[[New Name]]" in linker and "[[New Name|here]]" in linker


def test_rename_without_redirect_or_link_update(client, auth):
    auth.register()
    edit(client, "a", "content", title="A")
    edit(client, "linker", "[[A]]", title="Linker")
    rename(client, "a", "B", redirect=False, links=False)
    assert client.get("/wiki/a").status_code == 404
    assert "wikilink missing" in client.get("/wiki/linker").data.decode()
    assert "A" in client.get("/special/wanted").data.decode()


def test_rename_into_existing_page_fails(client, auth):
    auth.register()
    edit(client, "a", "a", title="A")
    edit(client, "b", "b", title="B")
    r = rename(client, "a", "B")
    assert r.status_code == 200 and "already exists" in r.data.decode()
    assert client.get("/wiki/a").status_code == 200


def test_rename_over_deleted_page_keeps_its_history(client, auth, app):
    auth.register()  # admin
    edit(client, "b", "old b", title="B")
    client.post("/wiki/b/delete")
    edit(client, "a", "a content", title="A")
    r = rename(client, "a", "B")
    assert r.status_code == 302
    assert "a content" in client.get("/wiki/b").data.decode()
    from wikiman.db import get_db
    with app.app_context():
        parked = get_db().execute("SELECT slug FROM pages WHERE slug LIKE 'b--deleted-%'").fetchone()
    assert parked is not None


def test_title_only_change(client, auth):
    auth.register()
    edit(client, "iphone", "x", title="Iphone")
    r = rename(client, "iphone", "iPhone")
    assert r.headers["Location"].endswith("/wiki/iphone")
    assert ">iPhone<" in client.get("/wiki/iphone").data.decode()
    assert "already its title" in rename(client, "iphone", "iPhone").data.decode()


def test_double_redirects_get_fixed(client, auth):
    auth.register()
    edit(client, "a", "content", title="A")
    rename(client, "a", "B")      # a -> b
    rename(client, "b", "C")      # b -> c, and a must now point at c directly
    assert "[[C]]" in client.get("/wiki/a/edit").data.decode()
    r = client.get("/wiki/a")
    assert "/wiki/c" in r.headers["Location"]


def test_redirect_loops_dont_hang(client, auth):
    auth.register()
    edit(client, "x", "#REDIRECT [[Y]]", title="X")
    edit(client, "y", "#REDIRECT [[X]]", title="Y")
    r = client.get("/wiki/x")
    assert r.status_code == 200 and "is a redirect to" in r.data.decode()


def test_main_page_cannot_be_renamed(client, auth):
    auth.register()
    assert client.get("/wiki/main-page/rename").status_code == 403


def test_rename_requires_login_and_respects_protection(client, auth, app):
    auth.register("alice")
    edit(client, "doc", "x", title="Doc")
    edit(client, "locked", "links to [[Doc]]", title="Locked")
    client.post("/wiki/locked/protect")
    client.post("/wiki/doc/protect")
    auth.logout()
    assert client.get("/wiki/doc/rename").status_code == 302  # to login
    auth.register("bob")
    assert client.get("/wiki/doc/rename").status_code == 403
    auth.logout()
    auth.login("alice")
    client.post("/wiki/doc/protect")  # unprotect doc only
    auth.logout()
    auth.login("bob")
    r = rename(client, "doc", "Document")
    assert r.status_code == 302
    follow = client.get("/wiki/document").data.decode()
    assert "Locked" in follow  # reported as skipped
    # bob can't edit the protected page, so it keeps the old link, which still works via the redirect
    from wikiman.store import get_page
    with app.app_context():
        assert "[[Doc]]" in get_page("locked")["body"]
    assert 'href="/wiki/doc"' in client.get("/wiki/locked").data.decode()


def test_redirects_hidden_from_search_body_and_orphans(client, auth):
    auth.register()
    edit(client, "a", "unique-word-zzq", title="A")
    edit(client, "hub", "[[B]]", title="Hub")
    rename(client, "a", "B", links=False)
    results = client.get("/search?q=zzq").data.decode()
    assert results.count('class="result-title"') == 1
    assert ">A<" not in client.get("/special/orphans").data.decode()
