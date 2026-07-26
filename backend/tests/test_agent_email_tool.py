import json
from datetime import datetime, timezone

from bson import ObjectId

from app.advisor.agent import tools as tools_mod
from app.advisor.agent.tools import build_tools
from app.advisor import context as advisor_context


class FakeUsers:
    def __init__(self, doc):
        self.doc = doc

    def find_one(self, query):
        if self.doc and self.doc.get("_id") == query.get("_id"):
            return self.doc
        return None


class FakeDb:
    def __init__(self, doc):
        self.users = FakeUsers(doc)


def _tool(monkeypatch, user_doc, send=None):
    monkeypatch.setattr(tools_mod, "get_db", lambda: FakeDb(user_doc))
    monkeypatch.setattr(advisor_context, "bind_user", lambda _uid: None)
    if send is not None:
        monkeypatch.setattr(tools_mod, "send_email", send)
    by_name = {t.name: t for t in build_tools(str(user_doc["_id"]) if user_doc else "u1")}
    return by_name["send_chat_summary_email"]


def test_email_tool_requires_verified_email(monkeypatch):
    uid = ObjectId()
    tool = _tool(
        monkeypatch,
        {"_id": uid, "username": "a", "email": "a@example.com"},
    )
    payload = json.loads(
        tool.invoke({"subject": "s", "summary_markdown": "hello", "confirm": False})
    )
    assert payload["error"]["code"] == "email_not_verified"


def test_email_tool_preview_without_sending(monkeypatch):
    uid = ObjectId()
    sent = []
    tool = _tool(
        monkeypatch,
        {
            "_id": uid,
            "username": "a",
            "email": "a@example.com",
            "email_verified_at": datetime.now(timezone.utc),
        },
        send=lambda *a, **k: sent.append(a),
    )
    payload = json.loads(
        tool.invoke(
            {
                "subject": "今日摘要",
                "summary_markdown": "要点一",
                "confirm": False,
            }
        )
    )
    assert payload["needs_confirm"] is True
    assert payload["preview"]["to"] == "a@example.com"
    assert sent == []


def test_email_tool_sends_on_confirm(monkeypatch):
    uid = ObjectId()
    sent = []
    tool = _tool(
        monkeypatch,
        {
            "_id": uid,
            "username": "a",
            "email": "a@example.com",
            "email_verified_at": datetime.now(timezone.utc),
        },
        send=lambda *a, **k: sent.append(a),
    )
    payload = json.loads(
        tool.invoke(
            {
                "subject": "今日摘要",
                "summary_markdown": "要点一",
                "confirm": True,
            }
        )
    )
    assert payload["applied"] is True
    assert sent == [("a@example.com", "今日摘要", "要点一")]
