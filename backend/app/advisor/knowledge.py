"""Per-user agent knowledge base (always / on_demand)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import get_db

ALWAYS_BODY_LIMIT = 6000
BODY_LIMIT = 8000
TITLE_LIMIT = 80
DESC_LIMIT = 200
ON_DEMAND_ENABLED_LIMIT = 50
MODES = frozenset({"always", "on_demand"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chars(s: str) -> int:
    return len(s or "")


def public_item(doc: dict[str, Any], *, include_body: bool = True) -> dict[str, Any]:
    out = {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "mode": doc.get("mode"),
        "enabled": bool(doc.get("enabled")),
        "description": doc.get("description") or "",
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
    }
    if include_body:
        out["body"] = doc.get("body") or ""
    return out


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def validate_payload(
    payload: dict[str, Any],
    *,
    existing_enabled: list[dict[str, Any]],
    exclude_id: str | None,
) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    mode = str(payload.get("mode") or "").strip()
    enabled = bool(payload.get("enabled", True))
    description = str(payload.get("description") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if _chars(title) > TITLE_LIMIT:
        raise ValueError(f"标题不能超过 {TITLE_LIMIT} 字")
    if mode not in MODES:
        raise ValueError("mode 必须是 always 或 on_demand")
    if not body:
        raise ValueError("正文不能为空")
    if _chars(body) > BODY_LIMIT:
        raise ValueError(f"单条正文不能超过 {BODY_LIMIT} 字")
    if _chars(description) > DESC_LIMIT:
        raise ValueError(f"描述不能超过 {DESC_LIMIT} 字")
    if mode == "on_demand" and not description:
        raise ValueError("可选知识必须填写 description")

    others = [x for x in existing_enabled if x.get("id") != exclude_id]
    if enabled and mode == "always":
        total = _chars(body) + sum(
            _chars(str(x.get("body") or ""))
            for x in others
            if x.get("mode") == "always" and x.get("enabled")
        )
        if total > ALWAYS_BODY_LIMIT:
            raise ValueError(
                f"必选知识启用正文合计不能超过 {ALWAYS_BODY_LIMIT} 字（当前将达到 {total}）"
            )
    if enabled and mode == "on_demand":
        n = 1 + sum(
            1
            for x in others
            if x.get("mode") == "on_demand" and x.get("enabled")
        )
        if n > ON_DEMAND_ENABLED_LIMIT:
            raise ValueError(
                f"启用的可选知识不能超过 {ON_DEMAND_ENABLED_LIMIT} 条"
            )
    return {
        "title": title,
        "mode": mode,
        "enabled": enabled,
        "description": description,
        "body": body,
    }


def format_always_knowledge_section(items: list[dict[str, Any]]) -> str:
    always = [
        x for x in items if x.get("enabled") and x.get("mode") == "always"
    ]
    if not always:
        return ""
    parts: list[str] = ["## 用户必选知识"]
    for x in always:
        parts.append(f"### {x.get('title')}\n{x.get('body') or ''}")
    return "\n\n".join(parts).strip()


def format_on_demand_catalog_section(items: list[dict[str, Any]]) -> str:
    optional = [
        x for x in items if x.get("enabled") and x.get("mode") == "on_demand"
    ]
    if not optional:
        return ""
    parts: list[str] = [
        "## 用户可选知识目录",
        "需要细则时调用 load_knowledge(id)；勿编造目录外知识；"
        "必选知识已在消息上下文中（若有），无需对必选条目重复加载。",
    ]
    for x in optional:
        parts.append(
            f"- id: {x.get('id')} | title: {x.get('title')} | desc: {x.get('description') or ''}"
        )
    return "\n\n".join(parts).strip()


def format_knowledge_prompt_section(items: list[dict[str, Any]]) -> str:
    """Build the system-prompt section containing only optional knowledge."""
    return format_on_demand_catalog_section(items)


def _col():
    return get_db().user_knowledge_items


def list_raw(user_id: str) -> list[dict[str, Any]]:
    return list(
        _col().find({"user_id": user_id}, {"_id": 0}).sort("updated_at", -1)
    )


def list_items(user_id: str, *, summary: bool = False) -> list[dict[str, Any]]:
    return [
        public_item(d, include_body=not summary) for d in list_raw(user_id)
    ]


def get_item(user_id: str, item_id: str) -> dict[str, Any] | None:
    doc = _col().find_one({"user_id": user_id, "id": item_id}, {"_id": 0})
    return public_item(doc) if doc else None


def create_item(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    existing = list_raw(user_id)
    clean = validate_payload(
        payload, existing_enabled=existing, exclude_id=None
    )
    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        **clean,
        "created_at": now,
        "updated_at": now,
    }
    _col().insert_one(doc)
    doc.pop("_id", None)
    return public_item(doc)


def update_item(
    user_id: str, item_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    existing = list_raw(user_id)
    if not any(x.get("id") == item_id for x in existing):
        raise KeyError(item_id)
    clean = validate_payload(
        payload, existing_enabled=existing, exclude_id=item_id
    )
    now = _now()
    _col().update_one(
        {"user_id": user_id, "id": item_id},
        {"$set": {**clean, "updated_at": now}},
    )
    doc = _col().find_one({"user_id": user_id, "id": item_id}, {"_id": 0})
    assert doc is not None
    return public_item(doc)


def delete_item(user_id: str, item_id: str) -> bool:
    res = _col().delete_one({"user_id": user_id, "id": item_id})
    return res.deleted_count > 0


def summarize_item(
    doc: dict[str, Any], *, include_body: bool = False
) -> dict[str, Any]:
    return public_item(doc, include_body=include_body)


def match_by_title(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = [x for x in items if q in str(x.get("title") or "").lower()]

    def _sort_key(doc: dict[str, Any]) -> Any:
        return doc.get("updated_at") or ""

    return sorted(hits, key=_sort_key, reverse=True)


def find_by_title(user_id: str, query: str) -> list[dict[str, Any]]:
    return match_by_title(list_raw(user_id), query)


def build_knowledge_prompt_section(user_id: str) -> str:
    return format_knowledge_prompt_section(list_raw(user_id))


def build_always_knowledge_text(user_id: str) -> str:
    return format_always_knowledge_section(list_raw(user_id))
