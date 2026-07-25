"""Per-user agent system prompt appended after the product prompt."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import get_db

SYSTEM_PROMPT_LIMIT = 6000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _col():
    return get_db().user_agent_config


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def validate_system_prompt(text: str) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) > SYSTEM_PROMPT_LIMIT:
        raise ValueError(
            f"系统提示词不能超过 {SYSTEM_PROMPT_LIMIT} 字（当前 {len(cleaned)}）"
        )
    return cleaned


def get_system_prompt(user_id: str) -> str:
    doc = _col().find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        return ""
    return str(doc.get("system_prompt") or "")


def public_system_prompt(user_id: str) -> dict[str, Any]:
    doc = _col().find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        return {"system_prompt": "", "updated_at": None}
    return {
        "system_prompt": str(doc.get("system_prompt") or ""),
        "updated_at": _iso(doc.get("updated_at")),
    }


def save_system_prompt(user_id: str, text: str) -> dict[str, Any]:
    cleaned = validate_system_prompt(text)
    now = _now()
    _col().update_one(
        {"user_id": user_id},
        {"$set": {"system_prompt": cleaned, "updated_at": now}},
        upsert=True,
    )
    return public_system_prompt(user_id)
