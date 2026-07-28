from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.advisor.monitor import store as store_mod
from app.advisor.monitor.models import CreateJobBody


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _DeleteResult:
    def __init__(self, n):
        self.deleted_count = n


class _FakeJobs:
    def __init__(self):
        self.docs: list[dict] = []

    def count_documents(self, q):
        return sum(1 for d in self.docs if d.get("user_id") == q.get("user_id"))

    def insert_one(self, doc):
        body = dict(doc)
        body["_id"] = ObjectId()
        self.docs.append(body)
        return _InsertResult(body["_id"])

    def find(self, q):
        matched = [
            d
            for d in self.docs
            if all(d.get(k) == v for k, v in q.items())
        ]

        class _Cur:
            def __init__(self, items):
                self._items = items

            def sort(self, *_a, **_k):
                return self

            def __iter__(self):
                return iter(self._items)

        return _Cur(matched)

    def find_one(self, q):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None

    def find_one_and_update(self, q, update, return_document=None):
        doc = self.find_one(q)
        if not doc:
            return None
        doc.update(update.get("$set") or {})
        return doc

    def update_one(self, q, update):
        doc = self.find_one(q)
        if doc:
            doc.update(update.get("$set") or {})

    def delete_one(self, q):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in q.items()):
                self.docs.pop(i)
                return _DeleteResult(1)
        return _DeleteResult(0)


class _FakeUsers:
    def __init__(self, doc=None):
        self.doc = doc

    def find_one(self, q):
        if self.doc and self.doc.get("_id") == q.get("_id"):
            return self.doc
        return None


class _FakeDB:
    def __init__(self, user_doc=None):
        self.agent_monitor_jobs = _FakeJobs()
        self.users = _FakeUsers(user_doc)


def _uid():
    return ObjectId()


def test_create_requires_verified_email(monkeypatch):
    uid = _uid()
    db = _FakeDB({"_id": uid, "email": "a@x.com"})
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    with pytest.raises(ValueError, match="邮箱"):
        store_mod.create_job(
            str(uid),
            {
                "title": "跌破提醒",
                "scope": "symbols",
                "symbols": ["510300"],
                "rules": [{"type": "price_below", "value": 4.0}],
            },
        )


def test_create_pause_resume_delete(monkeypatch):
    uid = _uid()
    db = _FakeDB(
        {
            "_id": uid,
            "email": "a@example.com",
            "email_verified_at": datetime.now(timezone.utc),
        }
    )
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    job = store_mod.create_job(
        str(uid),
        CreateJobBody(
            title="跌破4",
            scope="symbols",
            symbols=["510300"],
            rules=[{"type": "price_below", "value": 4.0}],
        ),
    )
    assert job["status"] == "running"
    assert job["notify_email"] == "a@example.com"
    assert job["llm_enabled"] is False
    assert len(job["rules"]) == 1
    assert job["rules"][0]["id"]

    paused = store_mod.pause_job(str(uid), job["id"])
    assert paused["status"] == "paused"
    resumed = store_mod.resume_job(str(uid), job["id"])
    assert resumed["status"] == "running"
    store_mod.delete_job(str(uid), job["id"])
    assert store_mod.get_job(str(uid), job["id"]) is None


def test_max_jobs(monkeypatch):
    uid = _uid()
    db = _FakeDB(
        {
            "_id": uid,
            "email": "a@example.com",
            "email_verified_at": datetime.now(timezone.utc),
        }
    )
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    for i in range(store_mod.JOBS_MAX_PER_USER):
        store_mod.create_job(
            str(uid),
            {
                "title": f"t{i}",
                "scope": "symbols",
                "symbols": ["510300"],
                "rules": [{"type": "price_below", "value": 1}],
            },
        )
    with pytest.raises(ValueError, match="上限"):
        store_mod.create_job(
            str(uid),
            {
                "title": "overflow",
                "scope": "symbols",
                "symbols": ["510300"],
                "rules": [{"type": "price_below", "value": 1}],
            },
        )


def test_resolve_symbols_watchlist(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.watchlist.load_watchlist",
        lambda uid: {"items": [{"symbol": "510300"}, {"symbol": "159915"}]},
    )
    out = store_mod.resolve_symbols(
        {"scope": "watchlist", "user_id": "u1", "symbols": []}
    )
    assert out == ["510300", "159915"]
