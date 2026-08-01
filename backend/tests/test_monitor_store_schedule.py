from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

from app.advisor.monitor import logs as logs_mod
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
            if all(d.get(k) == v for k, v in q.items() if not isinstance(v, dict))
        ]

        class _Cur:
            def __init__(self, items):
                self._items = items

            def sort(self, *_a, **_k):
                return self

            def limit(self, n):
                self._items = self._items[:n]
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


class _FakeLogs:
    def __init__(self):
        self.docs: list[dict] = []

    def insert_one(self, doc):
        body = dict(doc)
        body["_id"] = ObjectId()
        self.docs.append(body)
        return _InsertResult(body["_id"])

    def find(self, q):
        matched = []
        for d in self.docs:
            ok = True
            for k, v in q.items():
                if isinstance(v, dict) and "$gt" in v:
                    if not (d.get(k) and d.get(k) > v["$gt"]):
                        ok = False
                        break
                elif d.get(k) != v:
                    ok = False
                    break
            if ok:
                matched.append(d)

        class _Cur:
            def __init__(self, items):
                self._items = items

            def sort(self, key, direction):
                rev = direction < 0
                self._items = sorted(self._items, key=lambda x: x.get(key), reverse=rev)
                return self

            def limit(self, n):
                self._items = self._items[:n]
                return self

            def __iter__(self):
                return iter(self._items)

        return _Cur(matched)

    def delete_many(self, q):
        self.docs = [
            d
            for d in self.docs
            if not all(d.get(k) == v for k, v in q.items())
        ]


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
        self.agent_monitor_job_logs = _FakeLogs()
        self.users = _FakeUsers(user_doc)


def _uid():
    return ObjectId()


def _verified_db():
    uid = _uid()
    db = _FakeDB(
        {
            "_id": uid,
            "email": "a@example.com",
            "email_verified_at": datetime.now(timezone.utc),
        }
    )
    return uid, db


def test_create_once_watch_sets_schedule(monkeypatch):
    uid, db = _verified_db()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    monkeypatch.setattr(logs_mod, "get_db", lambda: db)
    monkeypatch.setattr(
        "app.advisor.monitor.schedule.is_trading_day",
        lambda d: True,
    )
    job = store_mod.create_job(
        str(uid),
        CreateJobBody(
            title="明天盯盘",
            scope="symbols",
            symbols=["510300"],
            rules=[{"type": "price_below", "value": 4.0}],
            kind="watch",
            repeat="once",
            calendar="trading_days",
            anchor_date="2026-07-30",
        ),
        now=datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
    )
    assert job["status"] == "scheduled"
    assert job["kind"] == "watch"
    assert job["repeat"] == "once"
    assert job["anchor_date"] == "2026-07-30"
    assert job["run_time"] == "09:15"
    assert job["end_time"] == "15:05"
    assert job["next_run_at"]
    assert job["end_at"]
    assert "2026-07-30" in job["next_run_at"] or "09:15" in job["next_run_at"]
    logs = logs_mod.list_job_logs(str(uid), job["id"])
    assert logs and logs[0]["event"] == "created"


def test_normalize_legacy_job():
    raw = {
        "_id": ObjectId(),
        "user_id": "u1",
        "title": "旧任务",
        "status": "running",
        "rules": [],
    }
    out = store_mod.normalize_legacy_job(raw)
    assert out["kind"] == "watch"
    assert out["repeat"] == "recurring"
    assert out["calendar"] == "trading_days"
    assert out["status"] == "running"
    assert out["run_time"] == "09:15"


def test_list_due_scheduled_jobs(monkeypatch):
    uid, db = _verified_db()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    monkeypatch.setattr(logs_mod, "get_db", lambda: db)
    past = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc)
    future = datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc)
    db.agent_monitor_jobs.insert_one(
        {
            "user_id": str(uid),
            "title": "due",
            "status": "scheduled",
            "kind": "watch",
            "next_run_at": past,
        }
    )
    db.agent_monitor_jobs.insert_one(
        {
            "user_id": str(uid),
            "title": "later",
            "status": "scheduled",
            "kind": "watch",
            "next_run_at": future,
        }
    )
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    due = store_mod.list_due_scheduled_jobs(now)
    assert len(due) == 1
    assert due[0]["title"] == "due"


def test_create_recurring_watch_during_session_starts_running(monkeypatch):
    uid, db = _verified_db()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    monkeypatch.setattr(logs_mod, "get_db", lambda: db)
    monkeypatch.setattr(
        "app.advisor.monitor.schedule.is_trading_day",
        lambda d: True,
    )
    job = store_mod.create_job(
        str(uid),
        CreateJobBody(
            title="盘中创建",
            scope="symbols",
            symbols=["510300"],
            rules=[{"type": "price_below", "value": 4.0}],
            kind="watch",
            repeat="recurring",
            calendar="trading_days",
        ),
        now=datetime(2026, 7, 31, 2, 30, tzinfo=timezone.utc),  # 10:30 SH
    )
    assert job["status"] == "running"
    assert job["next_run_at"] is None
    logs = logs_mod.list_job_logs(str(uid), job["id"])
    assert logs and "盘中已激活" in (logs[0]["message"] or "")


def test_create_pause_resume_scheduled(monkeypatch):
    uid, db = _verified_db()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    monkeypatch.setattr(logs_mod, "get_db", lambda: db)
    monkeypatch.setattr(
        "app.advisor.monitor.schedule.is_trading_day",
        lambda d: True,
    )
    job = store_mod.create_job(
        str(uid),
        {
            "title": "跌破4",
            "scope": "symbols",
            "symbols": ["510300"],
            "rules": [{"type": "price_below", "value": 4.0}],
        },
    )
    assert job["status"] == "scheduled"
    assert job["next_run_at"]
    assert job["kind"] == "watch"
    assert job["repeat"] == "recurring"

    paused = store_mod.pause_job(str(uid), job["id"])
    assert paused["status"] == "paused"
    resumed = store_mod.resume_job(str(uid), job["id"])
    assert resumed["status"] in ("scheduled", "running")
    store_mod.delete_job(str(uid), job["id"])
    assert store_mod.get_job(str(uid), job["id"]) is None
    assert db.agent_monitor_job_logs.docs == []
