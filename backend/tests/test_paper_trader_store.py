from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

from app.advisor.paper_trader import store as store_mod


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _FakeSessions:
    def __init__(self):
        self.docs: list[dict] = []

    def find_one(self, q):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                return d
        return None

    def insert_one(self, doc):
        body = dict(doc)
        body.setdefault("_id", ObjectId())
        self.docs.append(body)
        return _InsertResult(body["_id"])

    def update_one(self, q, update):
        doc = self.find_one(q)
        if doc:
            doc.update(update.get("$set") or {})

    def find_one_and_update(self, q, update, return_document=None):
        doc = self.find_one(q)
        if not doc:
            return None
        doc.update(update.get("$set") or {})
        return doc

    def find(self, q):
        status = q.get("status")
        nra = q.get("next_run_at")
        matched = []
        for d in self.docs:
            if status is not None and d.get("status") != status:
                continue
            if isinstance(nra, dict) and "$lte" in nra:
                val = d.get("next_run_at")
                if val is None or val > nra["$lte"]:
                    continue
            matched.append(d)

        class _Cur:
            def __init__(self, items):
                self._items = list(items)

            def sort(self, *_a, **_k):
                self._items.sort(key=lambda x: x.get("next_run_at") or datetime.min)
                return self

            def limit(self, n):
                self._items = self._items[:n]
                return self

            def __iter__(self):
                return iter(self._items)

        return _Cur(matched)


class _FakeDecisions:
    def __init__(self):
        self.docs: list[dict] = []

    def insert_one(self, doc):
        body = dict(doc)
        body.setdefault("_id", ObjectId())
        self.docs.append(body)
        return _InsertResult(body["_id"])

    def count_documents(self, q):
        return sum(1 for d in self.docs if d.get("user_id") == q.get("user_id"))

    def find(self, q):
        matched = [
            d for d in self.docs if d.get("user_id") == q.get("user_id")
        ]

        class _Cur:
            def __init__(self, items):
                self._items = list(items)

            def sort(self, *_a, **_k):
                return self

            def skip(self, n):
                self._items = self._items[n:]
                return self

            def limit(self, n):
                self._items = self._items[:n]
                return self

            def __iter__(self):
                return iter(self._items)

        return _Cur(matched)

    def find_one(self, q):
        for d in self.docs:
            if d.get("_id") == q.get("_id") and d.get("user_id") == q.get("user_id"):
                return d
        return None


class _FakeDB:
    def __init__(self):
        self.paper_trader_sessions = _FakeSessions()
        self.paper_trader_decisions = _FakeDecisions()
        self.users = type("U", (), {"find_one": lambda *a, **k: None})()


def test_start_pause_stop_roundtrip(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    monkeypatch.setattr(store_mod, "_peek_verified_email", lambda uid: None)

    user_id = "u_pt"
    s = store_mod.start_session(user_id)
    assert s["status"] == "running"
    assert s["mode"] == "signal_first"
    assert 300 <= int(s["interval_sec"]) <= 900
    store_mod.pause_session(user_id)
    assert store_mod.get_session(user_id)["status"] == "paused"
    store_mod.stop_session(user_id)
    assert store_mod.get_session(user_id)["next_run_at"] is None

    store_mod.touch_session(user_id, status="halted", halt_reason="test")
    try:
        store_mod.resume_session(user_id, confirm_halt_resume=False)
        assert False, "expected error"
    except ValueError as e:
        assert "confirm" in str(e).lower() or "halt" in str(e).lower()
    s2 = store_mod.resume_session(user_id, confirm_halt_resume=True)
    assert s2["status"] == "running"
    assert s2.get("halt_reason") in (None, "")


def test_insert_and_list_decisions(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    monkeypatch.setattr(store_mod, "_peek_verified_email", lambda uid: None)
    store_mod.start_session("u1")
    d = store_mod.insert_decision(
        {
            "user_id": "u1",
            "session_id": "s1",
            "started_at": datetime.now(timezone.utc),
            "mode": "signal_first",
            "orders_placed": [],
        }
    )
    assert d.get("id")
    listed = store_mod.list_decisions("u1")
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == d["id"]
