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
        if q.get("enabled") is True:
            return [dict(d) for d in self.docs if d.get("enabled")]
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
            real = next(d for d in self.docs if d.get("user_id") == q.get("user_id"))
            doc = real
        doc.update(body)


class _DB:
    def __init__(self):
        self.policy_watch_settings = _Coll()


def test_get_settings_defaults(monkeypatch):
    monkeypatch.setattr(settings_mod, "get_db", lambda: _DB())
    s = settings_mod.get_settings("u1")
    assert s["enabled"] is False
    assert s["sensitivity"] == "medium"
    assert s["preset_ids"] == ["gov_zhengce", "scio_news"]
    assert s["interval_trading_min"] == 15


def test_update_clamps_and_rejects_ninth_url(monkeypatch):
    db = _DB()
    monkeypatch.setattr(settings_mod, "get_db", lambda: db)
    monkeypatch.setattr(settings_mod, "peek_verified_email", lambda _uid: "a@b.c")
    monkeypatch.setattr(
        settings_mod,
        "is_url_safe_for_fetch",
        lambda url, allowed_ports=None: (True, ""),
    )
    out = settings_mod.update_settings("u1", {"interval_trading_min": 4, "enabled": True})
    assert out["interval_trading_min"] == 5
    assert out["notify_email"] == "a@b.c"
    assert out["source_status"]["gov_zhengce"]["state"] == "seeding"
    customs = [{"url": f"https://example.com/list/{i}"} for i in range(9)]
    try:
        settings_mod.update_settings("u1", {"custom_sources": customs})
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "8" in str(exc)


def test_reject_localhost(monkeypatch):
    monkeypatch.setattr(settings_mod, "get_db", lambda: _DB())
    monkeypatch.setattr(settings_mod, "peek_verified_email", lambda _uid: None)
    monkeypatch.setattr(
        settings_mod,
        "is_url_safe_for_fetch",
        lambda url, allowed_ports=None: (False, "禁止：目标为内网或本机地址"),
    )
    try:
        settings_mod.update_settings(
            "u1", {"custom_sources": [{"url": "http://127.0.0.1/"}]}
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "禁止" in str(exc)
