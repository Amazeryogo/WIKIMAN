import pytest

from wikiman import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "CSRF_DISABLED": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.db"),
    })


@pytest.fixture
def client(app):
    return app.test_client()


class Auth:
    def __init__(self, client):
        self.client = client

    def register(self, username="alice", password="password123"):
        return self.client.post("/auth/register", data={
            "username": username, "password": password, "confirm": password})

    def login(self, username="alice", password="password123"):
        return self.client.post("/auth/login", data={"username": username, "password": password})

    def logout(self):
        return self.client.post("/auth/logout")


@pytest.fixture
def auth(client):
    return Auth(client)


def edit(client, slug, body, title=None, summary="", base_rev=None):
    data = {"title": title or slug.replace("-", " ").title(), "body": body,
            "summary": summary, "save": "1"}
    if base_rev is not None:
        data["base_rev"] = str(base_rev)
    return client.post(f"/wiki/{slug}/edit", data=data)
