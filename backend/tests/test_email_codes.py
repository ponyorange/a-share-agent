from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app import email_codes as codes


class FakeCodesCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self._seq = 0

    def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.docs if self._match(doc, query)]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(
                    key=lambda d: d.get(key) or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return deepcopy(matches[0])

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
        self.docs = [doc for doc in self.docs if not self._match(doc, query)]

    def delete_many(self, query):
        self.docs = [doc for doc in self.docs if not self._match(doc, query)]

    def _match(self, doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if key == "_id":
                if actual != expected:
                    return False
                continue
            if isinstance(expected, dict):
                if "$gte" in expected:
                    if actual is None or actual < expected["$gte"]:
                        return False
                continue
            if actual != expected:
                return False
        return True


class FakeDb:
    def __init__(self):
        self.email_verification_codes = FakeCodesCollection()


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(codes, "get_db", lambda: db)
    return db


def test_code_roundtrip(fake_db):
    code = codes.create_and_store_code("u1", "a@example.com", codes.PURPOSE_BIND_EMAIL)
    assert len(code) == 6 and code.isdigit()
    codes.verify_code("u1", "a@example.com", codes.PURPOSE_BIND_EMAIL, code)


def test_wrong_code_raises(fake_db):
    codes.create_and_store_code("u1", "a@example.com", codes.PURPOSE_BIND_EMAIL)
    with pytest.raises(RuntimeError, match="code_invalid"):
        codes.verify_code("u1", "a@example.com", codes.PURPOSE_BIND_EMAIL, "000000")


def test_rate_limit(fake_db):
    codes.create_and_store_code("u1", "a@example.com", codes.PURPOSE_BIND_EMAIL)
    with pytest.raises(RuntimeError, match="code_rate_limited"):
        codes.create_and_store_code("u1", "a@example.com", codes.PURPOSE_BIND_EMAIL)


def test_expired_code(fake_db, monkeypatch):
    fixed = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(codes, "_utcnow", lambda: fixed)
    code = codes.create_and_store_code("u1", "a@example.com", codes.PURPOSE_BIND_EMAIL)
    monkeypatch.setattr(codes, "_utcnow", lambda: fixed + timedelta(minutes=11))
    with pytest.raises(RuntimeError, match="code_expired"):
        codes.verify_code("u1", "a@example.com", codes.PURPOSE_BIND_EMAIL, code)
