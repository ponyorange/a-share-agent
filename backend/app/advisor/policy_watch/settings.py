"""Per-user policy radar settings."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...db import get_db
from ..agent.web_fetch import is_url_safe_for_fetch
from ..monitor.store import require_verified_email
from .config import policy_watch_config
from .schedule import clamp_interval
from .urls import normalize_url_key

DEFAULT_PRESET_IDS = ["gov_zhengce", "scio_news"]
_SENSITIVITY = frozenset({"low", "medium", "high"})
_SCAN_MODES = frozenset({"always", "trading_only", "offhours_only"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value) if value else None


def peek_verified_email(user_id: str) -> str | None:
    try:
        return require_verified_email(user_id)
    except Exception:
        return None


def default_settings(user_id: str) -> dict[str, Any]:
    cfg = policy_watch_config()
    return {
        "user_id": user_id,
        "enabled": False,
        "sensitivity": "medium",
        "scan_mode": "always",
        "interval_trading_min": int(cfg.get("default_interval_trading") or 15),
        "interval_offhours_min": int(cfg.get("default_interval_offhours") or 60),
        "preset_ids": list(DEFAULT_PRESET_IDS),
        "custom_sources": [],
        "notify_email": None,
        "source_status": {},
        "last_fanout_at": None,
        "last_error": None,
        "created_at": None,
        "updated_at": None,
    }


def public_settings(doc: dict[str, Any]) -> dict[str, Any]:
    base = default_settings(str(doc.get("user_id") or ""))
    merged = {**base, **{k: v for k, v in doc.items() if k != "_id"}}
    merged["last_fanout_at"] = _iso(merged.get("last_fanout_at"))
    merged["created_at"] = _iso(merged.get("created_at"))
    merged["updated_at"] = _iso(merged.get("updated_at"))
    status = merged.get("source_status") or {}
    cleaned: dict[str, Any] = {}
    for key, raw in status.items():
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if item.get("last_ok_at") is not None:
            item["last_ok_at"] = _iso(item.get("last_ok_at"))
        cleaned[str(key)] = item
    merged["source_status"] = cleaned
    return merged


def get_settings(user_id: str) -> dict[str, Any]:
    doc = get_db().policy_watch_settings.find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        return default_settings(user_id)
    return public_settings(doc)


def list_enabled_settings() -> list[dict[str, Any]]:
    rows = get_db().policy_watch_settings.find({"enabled": True})
    return [public_settings(dict(row)) for row in rows]


def touch_settings(user_id: str, **fields: Any) -> None:
    payload = dict(fields)
    payload["updated_at"] = _now()
    get_db().policy_watch_settings.update_one(
        {"user_id": user_id},
        {"$set": payload},
        upsert=True,
    )


def _source_keys(settings: dict[str, Any]) -> set[str]:
    keys = {str(x) for x in (settings.get("preset_ids") or []) if str(x).strip()}
    for item in settings.get("custom_sources") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url:
            keys.add(normalize_url_key(url))
    return keys


def _normalize_custom(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("custom_sources 必须是列表")
    cfg = policy_watch_config()
    cap = int(cfg.get("max_custom_sources") or 8)
    if len(raw) > cap:
        raise ValueError(f"自定义栏目最多 {cap} 条")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("自定义栏目格式无效")
        url = str(item.get("url") or "").strip()
        if not url:
            raise ValueError("自定义栏目缺少 URL")
        ok, reason = is_url_safe_for_fetch(url, allowed_ports=[80, 443])
        if not ok:
            raise ValueError(reason or "禁止：URL 不安全")
        entry: dict[str, Any] = {
            "id": str(item.get("id") or uuid4().hex[:10]),
            "url": url,
        }
        title = str(item.get("title") or "").strip()
        if title:
            entry["title"] = title
        out.append(entry)
    return out


def update_settings(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    current = get_settings(user_id)
    was_enabled = bool(current.get("enabled"))
    merged = dict(current)
    payload = body or {}

    if "enabled" in payload:
        merged["enabled"] = bool(payload["enabled"])
    if "sensitivity" in payload:
        level = str(payload.get("sensitivity") or "").strip()
        if level not in _SENSITIVITY:
            raise ValueError("灵敏度无效")
        merged["sensitivity"] = level
    if "scan_mode" in payload:
        mode = str(payload.get("scan_mode") or "").strip()
        if mode not in _SCAN_MODES:
            raise ValueError("扫描时段无效")
        merged["scan_mode"] = mode
    if "interval_trading_min" in payload:
        merged["interval_trading_min"] = clamp_interval(
            payload.get("interval_trading_min"), kind="trading"
        )
    if "interval_offhours_min" in payload:
        merged["interval_offhours_min"] = clamp_interval(
            payload.get("interval_offhours_min"), kind="offhours"
        )
    if "preset_ids" in payload:
        raw_ids = payload.get("preset_ids") or []
        if not isinstance(raw_ids, list):
            raise ValueError("preset_ids 必须是列表")
        merged["preset_ids"] = [str(x).strip() for x in raw_ids if str(x).strip()]
    if "custom_sources" in payload:
        merged["custom_sources"] = _normalize_custom(payload.get("custom_sources"))

    old_keys = _source_keys(current)
    new_keys = _source_keys(merged)
    status = {
        str(k): dict(v)
        for k, v in (current.get("source_status") or {}).items()
        if isinstance(v, dict)
    }
    to_seed = set(new_keys) if merged["enabled"] and not was_enabled else (new_keys - old_keys)
    if merged["enabled"]:
        for key in to_seed:
            prev = dict(status.get(key) or {})
            prev["state"] = "seeding"
            status[key] = prev
    merged["source_status"] = status

    if merged["enabled"]:
        merged["notify_email"] = peek_verified_email(user_id)

    now = _now()
    if current.get("created_at") is None:
        merged["created_at"] = now
    merged["updated_at"] = now
    merged["user_id"] = user_id

    persist = dict(merged)
    get_db().policy_watch_settings.update_one(
        {"user_id": user_id},
        {"$set": persist},
        upsert=True,
    )
    return public_settings(persist)


def clear_seeding(source_key: str) -> None:
    """Mark a source as ok after the first seed scan (used by discover)."""
    key = str(source_key or "").strip()
    if not key:
        return
    for row in list_enabled_settings():
        status = dict(row.get("source_status") or {})
        cur = status.get(key)
        if not isinstance(cur, dict) or cur.get("state") != "seeding":
            continue
        status[key] = {**cur, "state": "ok", "last_ok_at": _now(), "last_error": None}
        touch_settings(str(row.get("user_id") or ""), source_status=status)
