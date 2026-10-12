import re

from conftest import edit
from wikiman import create_app
from wikiman.markup import diff_lines, render, slugify


def test_slugify():
    assert slugify("Main Page") == "main-page"
    assert slugify("  C++ / Rust: a story!  ") == "c-rust-a-story"
    assert slugify("Ünïcödé Title") == "ünïcödé-title"
    assert slugify("!!!") == ""


def test_render_wikilinks_and_sanitising():
    html, links = render("See [[Foo Bar]] and [[baz|the baz]]. <script>alert(1)</script>",
                         resolver=lambda s: s == "foo-bar")
    assert 'href="/wiki/foo-bar"' in html and 'class="wikilink"' in html
    assert "wikilink missing" in html and ">the baz<" in html
    assert "<script>" not in html
    assert links == {"foo-bar", "baz"}


def test_render_blocks_js_urls_and_keeps_code():
    html, links = render("[x](javascript:alert(1))\n\n`[[not a link]]`")
    assert "javascript:" not in html
    assert "[[not a link]]" in html and not links


def test_render_features():
    html, _ = render("[TOC]\n\n## One\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n~~gone~~\n\n```py\nx = 1\n```")
    assert 'class="toc"' in html and "<table>" in html and "<del>gone</del>" in html
    assert "<pre><code" in html


def test_diff_lines():
    rows = diff_lines("a\nb\nc\n", "a\nB\nc\nd\n")
    kinds = [r[0] for r in rows]
    assert kinds.count("add") == 2 and kinds.count("del") == 1


def test_main_page_seeded(client):
    r = client.get("/", follow_redirects=True)
    assert r.status_code == 200
    assert b"Welcome to" in r.data and b"Main Page" in r.data


def test_first_user_is_admin_and_second_is_not(client, auth, app):
    auth.register("alice")
    auth.logout()
    auth.register("bob")
    from wikiman.db import get_db
    with app.app_context():
        rows = {r["username"]: r["is_admin"] for r in get_db().execute("SELECT * FROM users")}
    assert rows == {"alice": 1, "bob": 0}


def test_registration_validation(client, auth):
    r = auth.register("x", "short")
    assert b"Username must be" in r.data
    auth.register("alice")
    auth.logout()
    r = auth.register("ALICE")
    assert b"taken" in r.data


def test_login_logout(client, auth):
    auth.register()
    auth.logout()
    r = auth.login(password="wrongwrong")
    assert b"Wrong username or password" in r.data
    r = auth.login()
    assert r.status_code == 302
    assert b"alice" in client.get("/wiki/main-page").data


def test_edit_requires_login(client):
    r = client.get("/wiki/new-page/edit")
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]


def test_create_view_and_red_links(client, auth):
    auth.register()
    r = edit(client, "alpha", "Hello, see [[Beta]].", title="Alpha")
    assert r.status_code == 302
    page = client.get("/wiki/alpha").data.decode()
    assert "Hello" in page and "wikilink missing" in page
    assert client.get("/wiki/beta").status_code == 404
    edit(client, "beta", "I am beta.", title="Beta")
    page = client.get("/wiki/alpha").data.decode()
    assert "wikilink missing" not in page
    # backlinks on beta
    assert "Alpha" in client.get("/wiki/beta").data.decode()


def test_non_canonical_url_redirects(client):
    r = client.get("/wiki/Main_Page")
    assert r.status_code == 301 and r.headers["Location"].endswith("/wiki/main-page")


def test_title_must_match_slug(client, auth):
    auth.register()
    r = edit(client, "alpha", "x", title="Something Else")
    assert r.status_code == 200 and b"has to match" in r.data


def test_history_diff_and_revert(client, auth, app):
    auth.register()
    edit(client, "doc", "line one\n", title="Doc", summary="first")
    edit(client, "doc", "line one\nline two\n", title="Doc", summary="second")
    hist = client.get("/wiki/doc/history").data.decode()
    assert "first" in hist and "second" in hist
    revs = [int(x) for x in re.findall(r"/wiki/doc/rev/(\d+)", hist)]
    newest, oldest = max(revs), min(revs)
    d = client.get(f"/wiki/doc/diff?from={oldest}&to={newest}").data.decode()
    assert "line two" in d and 'class="add"' in d
    client.post(f"/wiki/doc/revert/{oldest}")
    assert "line two" not in client.get("/wiki/doc").data.decode()
    assert "Reverted to revision" in client.get("/wiki/doc/history").data.decode()


def test_no_op_edit_creates_no_revision(client, auth):
    auth.register()
    edit(client, "doc", "same", title="Doc")
    r = edit(client, "doc", "same", title="Doc")
    client.get("/wiki/doc")  # consume flash
    hist = client.get("/wiki/doc/history").data.decode()
    assert hist.count("/wiki/doc/rev/") == 1


def test_edit_conflict(client, auth, app):
    auth.register()
    edit(client, "doc", "v1", title="Doc")
    from wikiman.db import get_db
    with app.app_context():
        base = get_db().execute("SELECT current_rev_id FROM pages WHERE slug='doc'").fetchone()[0]
    edit(client, "doc", "v2 from someone", title="Doc", base_rev=base)
    r = edit(client, "doc", "v2 from me", title="Doc", base_rev=base)
    assert r.status_code == 200 and b"Someone else saved" in r.data
    assert b"v2 from me" in r.data
    assert "v2 from someone" in client.get("/wiki/doc").data.decode()


def test_search(client, auth):
    auth.register()
    edit(client, "fruit", "Bananas are yellow and apples are red.", title="Fruit")
    edit(client, "cars", "Engines and wheels.", title="Cars")
    r = client.get("/search?q=banana").data.decode()
    assert "Fruit" in r and "<mark>" in r and "Cars</a>" not in r
    # prefix + stemming
    assert "Fruit" in client.get("/search?q=appl").data.decode()
    # odd input doesn't crash FTS
    assert client.get('/search?q=%22%28*AND%29').status_code == 200
    # go=1 jumps to exact title
    r = client.get("/search?q=Cars&go=1")
    assert r.status_code == 302 and r.headers["Location"].endswith("/wiki/cars")


def test_search_reindexes_on_edit(client, auth):
    auth.register()
    edit(client, "p", "zebra", title="P")
    edit(client, "p", "giraffe", title="P")
    assert "No page" in client.get("/search?q=zebra").data.decode() or \
        "0 results" in client.get("/search?q=zebra").data.decode()
    assert "/wiki/p" in client.get("/search?q=giraffe").data.decode()


def test_special_pages(client, auth):
    auth.register()
    edit(client, "a", "[[Missing One]] [[Missing One]] [[B]]", title="A")
    edit(client, "b", "b", title="B")
    edit(client, "lonely", "nobody links me", title="Lonely")
    assert "Missing one" in client.get("/special/wanted").data.decode()
    orphans = client.get("/special/orphans").data.decode()
    assert "Lonely" in orphans and ">B<" not in orphans
    allp = client.get("/special/all").data.decode()
    assert "Lonely" in allp and "Main Page" in allp
    rc = client.get("/special/recent").data.decode()
    assert "Lonely" in rc and "alice" in rc
    assert client.get("/special/random").status_code == 302
    assert "Lonely" in client.get("/user/alice").data.decode()


def test_admin_protect_and_delete(client, auth):
    auth.register("alice")  # admin
    edit(client, "doc", "content", title="Doc")
    client.post("/wiki/doc/protect")
    auth.logout()
    auth.register("bob")
    assert client.get("/wiki/doc/edit").status_code == 403
    assert client.post("/wiki/doc/delete").status_code == 403
    auth.logout()
    auth.login("alice")
    client.post("/wiki/doc/delete")
    r = client.get("/wiki/doc")
    assert r.status_code == 404 and b"deleted" in r.data
    assert "/wiki/doc" not in client.get("/search?q=content").data.decode()
    # recreating restores history
    edit(client, "doc", "back again", title="Doc")
    assert client.get("/wiki/doc/history").data.decode().count("/wiki/doc/rev/") == 2


def test_main_page_cannot_be_deleted(client, auth):
    auth.register()
    client.post("/wiki/main-page/delete")
    assert client.get("/wiki/main-page").status_code == 200


def test_preview_api(client, auth):
    auth.register()
    r = client.post("/api/preview", data={"body": "**hi** [[Nope]]"})
    assert "<strong>hi</strong>" in r.json["html"] and "missing" in r.json["html"]


def test_csrf_enforced(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(tmp_path / "c.db")})
    c = app.test_client()
    r = c.post("/auth/register", data={"username": "eve", "password": "password123",
                                        "confirm": "password123"})
    assert r.status_code == 400
    page = c.get("/auth/register").data.decode()
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = c.post("/auth/register", data={"username": "eve", "password": "password123",
                                        "confirm": "password123", "csrf_token": token})
    assert r.status_code == 302


def test_private_wiki(tmp_path):
    app = create_app({"TESTING": True, "CSRF_DISABLED": True, "SECRET_KEY": "x",
                      "PRIVATE": True, "DATABASE": str(tmp_path / "p.db")})
    c = app.test_client()
    r = c.get("/wiki/main-page")
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]
    assert c.get("/auth/login").status_code == 200


def test_closed_registration(tmp_path):
    app = create_app({"TESTING": True, "CSRF_DISABLED": True, "SECRET_KEY": "x",
                      "ALLOW_REGISTRATION": False, "DATABASE": str(tmp_path / "r.db")})
    c = app.test_client()
    # first user is still allowed so the wiki can be bootstrapped
    assert c.get("/auth/register").status_code == 200
    c.post("/auth/register", data={"username": "root", "password": "password123",
                                    "confirm": "password123"})
    c.post("/auth/logout")
    assert c.get("/auth/register").status_code == 403


def test_open_redirect_blocked(client, auth):
    auth.register()
    auth.logout()
    r = client.post("/auth/login?next=//evil.com", data={"username": "alice", "password": "password123"})
    assert "evil.com" not in r.headers["Location"]
