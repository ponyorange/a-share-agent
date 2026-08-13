from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app
from app.advisor.policy_watch import settings as settings_mod


class _Coll:
    def __init__(self):
        self.docs = []

    def find_one(self, q, proj=None):
        for d in self.docs:
            if d.get("user_id") == q.get("user_id"):
                return dict(d)
        return None

    def find(self, q):
        return [dict(d) for d in self.docs]

    def update_one(self, q, update, upsert=False):
        doc = self.find_one(q)
        body = update.get("$set") or {}
        if doc is None:
            if not upsert:
                return
            doc = {"user_id": q.get("user_id")}
            self.docs.append(doc)
        else:
            doc = next(d for d in self.docs if d.get("user_id") == q.get("user_id"))
        doc.update(body)


class _DB:
    def __init__(self):
        self.policy_watch_settings = _Coll()


def _auth():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}


def test_presets_require_auth():
    app.dependency_overrides.clear()
    assert TestClient(app).get("/api/advisor/policy-watch/presets").status_code == 401


def test_settings_get_and_clamp(monkeypatch):
    db = _DB()
    monkeypatch.setattr(settings_mod, "get_db", lambda: db)
    monkeypatch.setattr(settings_mod, "peek_verified_email", lambda _uid: "a@b.c")
    monkeypatch.setattr(
        "app.advisor.policy_watch.routes.public_llm_settings",
        lambda _uid: {"configured": False},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.routes.peek_verified_email",
        lambda _uid: "a@b.c",
    )
    monkeypatch.setattr(
        settings_mod,
        "is_url_safe_for_fetch",
        lambda url, allowed_ports=None: (
            (False, "禁止：目标为内网或本机地址")
            if "127.0.0.1" in url
            else (True, "")
        ),
    )
    _auth()
    try:
        client = TestClient(app)
        got = client.get("/api/advisor/policy-watch/settings")
        assert got.status_code == 200
        assert got.json()["sensitivity"] == "medium"
        put = client.put(
            "/api/advisor/policy-watch/settings",
            json={"interval_trading_min": 4, "enabled": True},
        )
        assert put.status_code == 200
        assert put.json()["interval_trading_min"] == 5
        ninth = client.put(
            "/api/advisor/policy-watch/settings",
            json={"custom_sources": [{"url": f"https://example.com/{i}"} for i in range(9)]},
        )
        assert ninth.status_code == 400
        local = client.put(
            "/api/advisor/policy-watch/settings",
            json={"custom_sources": [{"url": "http://127.0.0.1/"}]},
        )
        assert local.status_code == 400
    finally:
        app.dependency_overrides.clear()
