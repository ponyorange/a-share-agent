"""Persist agent chat sessions with sliding-window context."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ...db import get_db

# 上下文窗口：最近 N 条 user/assistant 消息进入模型（不含 system）
MAX_CONTEXT_MESSAGES = 16
# 单条消息送入模型时的最大字符
MAX_CONTEXT_CHARS = 3500
# 会话列表条数
MAX_SESSIONS = 40


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


def list_sessions(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    db = get_db()
    cur = (
        db.agent_chat_sessions.find({"user_id": user_id}, {"_id": 0})
        .sort("updated_at", -1)
        .limit(min(limit, MAX_SESSIONS))
    )
    out = []
    for doc in cur:
        out.append(
            {
                "session_id": doc.get("session_id"),
                "title": doc.get("title") or "新对话",
                "updated_at": (
                    doc["updated_at"].isoformat()
                    if hasattr(doc.get("updated_at"), "isoformat")
                    else doc.get("updated_at")
                ),
                "message_count": int(doc.get("message_count") or 0),
            }
        )
    return out


def ensure_session(user_id: str, session_id: str | None = None) -> str:
    db = get_db()
    sid = (session_id or "").strip() or new_session_id()
    now = _now()
    existing = db.agent_chat_sessions.find_one(
        {"user_id": user_id, "session_id": sid}, {"_id": 1}
    )
    if existing:
        return sid
    db.agent_chat_sessions.insert_one(
        {
            "user_id": user_id,
            "session_id": sid,
            "title": "新对话",
            "message_count": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    return sid


def session_exists(user_id: str, session_id: str) -> bool:
    db = get_db()
    return (
        db.agent_chat_sessions.find_one(
            {"user_id": user_id, "session_id": session_id},
            {"_id": 1},
        )
        is not None
    )


def get_messages(user_id: str, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    db = get_db()
    cur = (
        db.agent_chat_messages.find(
            {"user_id": user_id, "session_id": session_id},
            {"_id": 0},
        )
        .sort("created_at", 1)
        .limit(limit)
    )
    rows = []
    for doc in cur:
        rows.append(
            {
                "role": doc.get("role"),
                "content": doc.get("content") or "",
                "tool_trace": doc.get("tool_trace") or [],
                "created_at": (
                    doc["created_at"].isoformat()
                    if hasattr(doc.get("created_at"), "isoformat")
                    else doc.get("created_at")
                ),
            }
        )
    return rows


def append_message(
    user_id: str,
    session_id: str,
    *,
    role: str,
    content: str,
    tool_trace: list[dict[str, Any]] | None = None,
) -> None:
    db = get_db()
    now = _now()
    updates: dict[str, Any] = {"updated_at": now}
    sess = db.agent_chat_sessions.find_one(
        {"user_id": user_id, "session_id": session_id},
        {"title": 1},
    )
    if role == "user" and (
        not sess or sess.get("title") in (None, "", "新对话")
    ):
        updates["title"] = (content or "").strip().replace("\n", " ")[:36] or "新对话"
    result = db.agent_chat_sessions.update_one(
        {"user_id": user_id, "session_id": session_id},
        {
            "$set": updates,
            "$inc": {"message_count": 1},
        },
        upsert=False,
    )
    if result.matched_count == 0:
        return
    inserted = db.agent_chat_messages.insert_one(
        {
            "user_id": user_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "tool_trace": tool_trace or [],
            "created_at": now,
        }
    )
    # Close the TOCTOU window: session may be deleted between update and insert.
    if (
        db.agent_chat_sessions.find_one(
            {"user_id": user_id, "session_id": session_id},
            {"_id": 1},
        )
        is None
    ):
        inserted_id = getattr(inserted, "inserted_id", None)
        if inserted_id is not None:
            db.agent_chat_messages.delete_one({"_id": inserted_id})
        return



def delete_session(user_id: str, session_id: str) -> None:
    db = get_db()
    db.agent_chat_sessions.delete_one({"user_id": user_id, "session_id": session_id})
    db.agent_chat_messages.delete_many({"user_id": user_id, "session_id": session_id})


def build_context_history(
    user_id: str,
    session_id: str,
    *,
    max_messages: int = MAX_CONTEXT_MESSAGES,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> list[dict[str, str]]:
    """最近 N 条 user/assistant 文本，截断过长内容，供模型上下文。"""
    rows = get_messages(user_id, session_id, limit=500)
    usable = [r for r in rows if r.get("role") in ("user", "assistant") and r.get("content")]
    window = usable[-max_messages:]
    out: list[dict[str, str]] = []
    for r in window:
        text = str(r["content"])
        if len(text) > max_chars:
            text = text[: max_chars - 20] + "\n…(已截断)"
        out.append({"role": str(r["role"]), "content": text})
    return out
