from __future__ import annotations

from copy import deepcopy

from bson import ObjectId

from app.advisor.agent import chat_store


class FakeCollection:
    def __init__(self):
        self.documents: list[dict] = []

    def find_one(self, query, *_args):
        return next(
            (deepcopy(doc) for doc in self.documents if _matches(doc, query)),
            None,
        )

    def insert_one(self, document):
        stored = deepcopy(document)
        stored["_id"] = ObjectId()
        self.documents.append(stored)
        return type("InsertResult", (), {"inserted_id": stored["_id"]})()

    def update_one(self, query, update, upsert=False):
        del upsert
        for index, doc in enumerate(self.documents):
            if _matches(doc, query):
                updated = deepcopy(doc)
                updated.update(update.get("$set") or {})
                for key, amount in (update.get("$inc") or {}).items():
                    updated[key] = int(updated.get(key) or 0) + amount
                self.documents[index] = updated
                return type("UpdateResult", (), {"matched_count": 1})()
        return type("UpdateResult", (), {"matched_count": 0})()

    def delete_one(self, query):
        before = len(self.documents)
        self.documents = [doc for doc in self.documents if not _matches(doc, query)]
        return type("DeleteResult", (), {"deleted_count": before - len(self.documents)})()


class FakeDB:
    def __init__(self):
        self.agent_chat_sessions = FakeCollection()
        self.agent_chat_messages = FakeCollection()


def _matches(document, query):
    return all(document.get(key) == expected for key, expected in query.items())


def test_append_message_skips_when_session_missing(monkeypatch):
    database = FakeDB()
    monkeypatch.setattr(chat_store, "get_db", lambda: database)

    chat_store.append_message("u", "missing", role="assistant", content="reply")

    assert database.agent_chat_messages.documents == []


def test_append_message_deletes_orphan_if_session_deleted_after_update(monkeypatch):
    database = FakeDB()
    database.agent_chat_sessions.documents.append(
        {
            "_id": ObjectId(),
            "user_id": "u",
            "session_id": "s",
            "title": "对话",
            "message_count": 1,
        }
    )
    monkeypatch.setattr(chat_store, "get_db", lambda: database)

    original_insert = database.agent_chat_messages.insert_one

    def insert_then_delete_session(document):
        result = original_insert(document)
        database.agent_chat_sessions.documents.clear()
        return result

    monkeypatch.setattr(database.agent_chat_messages, "insert_one", insert_then_delete_session)

    chat_store.append_message("u", "s", role="assistant", content="late reply")

    assert database.agent_chat_sessions.documents == []
    assert database.agent_chat_messages.documents == []


def test_append_message_persists_when_session_remains(monkeypatch):
    database = FakeDB()
    database.agent_chat_sessions.documents.append(
        {
            "_id": ObjectId(),
            "user_id": "u",
            "session_id": "s",
            "title": "对话",
            "message_count": 1,
        }
    )
    monkeypatch.setattr(chat_store, "get_db", lambda: database)

    chat_store.append_message("u", "s", role="assistant", content="reply")

    assert len(database.agent_chat_messages.documents) == 1
    assert database.agent_chat_messages.documents[0]["content"] == "reply"
    assert database.agent_chat_sessions.documents[0]["message_count"] == 2
