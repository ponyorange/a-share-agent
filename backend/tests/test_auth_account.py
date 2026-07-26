from copy import deepcopy
from datetime import datetime, timezone

from bson import ObjectId
from fastapi.testclient import TestClient

from app import auth as auth_mod
from app import email_codes as codes_mod
from app.auth import get_current_user, hash_password
from app.main import app


class FakeUsers:
    def __init__(self):
        self.docs: dict[ObjectId, dict] = {}

    def find_one(self, query, projection=None):
        for doc in self.docs.values():
            if self._match(doc, query):
                return deepcopy(doc)
        return None

    def insert_one(self, doc):
        stored = deepcopy(doc)
        oid = stored.get("_id") or ObjectId()
        stored["_id"] = oid
        self.docs[oid] = stored
        return type("Res", (), {"inserted_id": oid})()

    def update_one(self, query, update, upsert=False):
        for oid, doc in self.docs.items():
            if self._match(doc, query):
                doc.update(deepcopy(update.get("$set", {})))
                return
        if upsert:
            raise AssertionError("upsert not expected in these tests")

    def _match(self, doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                continue
            if actual != expected:
                return False
        return True


class FakeCodes:
    def __init__(self):
        self.docs: list[dict] = []
        self._seq = 0

    def find_one(self, query, sort=None, projection=None):
        matches = [d for d in self.docs if self._match(d, query)]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return deepcopy(matches[0]) if matches else None

    def insert_one(self, doc):
        self._seq += 1
        stored = deepcopy(doc)
        stored["_id"] = self._seq
        self.docs.append(stored)
        return type("Res", (), {"inserted_id": self._seq})()

    def update_one(self, query, update):
        for doc in self.docs:
            if self._match(doc, query):
                doc.update(deepcopy(update.get("$set", {})))
                return

    def delete_one(self, query):
        self.docs = [d for d in self.docs if not self._match(d, query)]

    def delete_many(self, query):
        self.docs = [d for d in self.docs if not self._match(d, query)]

    def _match(self, doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict) and "$gte" in expected:
                if actual is None or actual < expected["$gte"]:
                    return False
                continue
            if actual != expected:
                return False
        return True


class FakeDb:
    def __init__(self):
        self.users = FakeUsers()
        self.email_verification_codes = FakeCodes()
        self.portfolios = type("C", (), {"update_one": lambda *a, **k: None})()
        self.paper_accounts = type("C", (), {"update_one": lambda *a, **k: None})()


def _seed_user(db: FakeDb, *, username="alice", password="pass1234", email=None, verified=False):
    oid = ObjectId()
    doc = {
        "_id": oid,
        "username": username,
        "password_hash": hash_password(password),
        "created_at": datetime.now(timezone.utc),
    }
    if email:
        doc["email"] = email
        if verified:
            doc["email_verified_at"] = datetime.now(timezone.utc)
    db.users.docs[oid] = doc
    return oid


def test_me_and_bind_email_flow(monkeypatch):
    db = FakeDb()
    uid = _seed_user(db)
    sent = []
    monkeypatch.setattr(auth_mod, "get_db", lambda: db)
    monkeypatch.setattr(codes_mod, "get_db", lambda: db)
    monkeypatch.setattr(auth_mod, "send_email", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(codes_mod.secrets, "randbelow", lambda n: 123456)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uid),
        "username": "alice",
        "email": None,
        "email_verified": False,
    }
    client = TestClient(app)
    try:
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["email"] is None
        assert me.json()["user"]["email_verified"] is False

        send = client.post(
            "/api/auth/account/email/send-code",
            json={"email": "Alice@Example.com"},
        )
        assert send.status_code == 200
        assert sent and sent[0][0] == "alice@example.com"
        assert "123456" in sent[0][2]

        verify = client.post(
            "/api/auth/account/email/verify",
            json={"email": "alice@example.com", "code": "123456"},
        )
        assert verify.status_code == 200, verify.json()
        assert verify.json()["user"]["email"] == "alice@example.com"
        assert verify.json()["user"]["email_verified"] is True
    finally:
        app.dependency_overrides.clear()


def test_email_taken(monkeypatch):
    db = FakeDb()
    uid = _seed_user(db)
    _seed_user(db, username="bob", email="taken@example.com", verified=True)
    monkeypatch.setattr(auth_mod, "get_db", lambda: db)
    monkeypatch.setattr(codes_mod, "get_db", lambda: db)
    monkeypatch.setattr(auth_mod, "send_email", lambda *a, **k: None)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uid),
        "username": "alice",
        "email": None,
        "email_verified": False,
    }
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/auth/account/email/send-code",
            json={"email": "taken@example.com"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "email_taken"
    finally:
        app.dependency_overrides.clear()


def test_change_password(monkeypatch):
    db = FakeDb()
    uid = _seed_user(db, password="oldpass1")
    monkeypatch.setattr(auth_mod, "get_db", lambda: db)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uid),
        "username": "alice",
        "email": None,
        "email_verified": False,
    }
    client = TestClient(app)
    try:
        bad = client.post(
            "/api/auth/account/password",
            json={"old_password": "wrong", "new_password": "newpass1"},
        )
        assert bad.status_code == 400
        assert bad.json()["detail"] == "password_incorrect"
        ok = client.post(
            "/api/auth/account/password",
            json={"old_password": "oldpass1", "new_password": "newpass1"},
        )
        assert ok.status_code == 200
        doc = db.users.docs[uid]
        assert auth_mod.verify_password("newpass1", doc["password_hash"])
    finally:
        app.dependency_overrides.clear()


def test_password_reset_flow(monkeypatch):
    db = FakeDb()
    uid = _seed_user(
        db,
        username="alice",
        password="oldpass1",
        email="alice@example.com",
        verified=True,
    )
    sent = []
    monkeypatch.setattr(auth_mod, "get_db", lambda: db)
    monkeypatch.setattr(codes_mod, "get_db", lambda: db)
    monkeypatch.setattr(auth_mod, "send_email", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(codes_mod.secrets, "randbelow", lambda n: 654321)
    client = TestClient(app)

    missing = client.post(
        "/api/auth/password-reset/send-code",
        json={"account": "nobody"},
    )
    assert missing.status_code == 200
    assert missing.json()["ok"] is True
    assert sent == []

    send = client.post(
        "/api/auth/password-reset/send-code",
        json={"account": "alice"},
    )
    assert send.status_code == 200
    assert sent and "654321" in sent[0][2]

    confirm = client.post(
        "/api/auth/password-reset/confirm",
        json={
            "account": "alice@example.com",
            "code": "654321",
            "new_password": "resetpass",
        },
    )
    assert confirm.status_code == 200, confirm.json()
    assert auth_mod.verify_password("resetpass", db.users.docs[uid]["password_hash"])


def test_password_reset_no_email_does_not_send(monkeypatch):
    db = FakeDb()
    _seed_user(db, username="alice", password="oldpass1")
    sent = []
    monkeypatch.setattr(auth_mod, "get_db", lambda: db)
    monkeypatch.setattr(auth_mod, "send_email", lambda *a, **k: sent.append(a))
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password-reset/send-code",
        json={"account": "alice"},
    )
    assert resp.status_code == 200
    assert sent == []
