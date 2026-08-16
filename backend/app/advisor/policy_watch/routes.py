"""HTTP routes for policy radar settings and inbox."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ...auth import get_current_user
from .. import context
from ..llm_settings import public_llm_settings
from .config import policy_watch_config
from .settings import get_settings, peek_verified_email, update_settings
from .store import enrich_source_status, list_items, mark_item_read

router = APIRouter(prefix="/policy-watch", tags=["policy-watch"])


def _uid(user: dict[str, Any] = Depends(get_current_user)) -> str:
    uid = str(user["id"])
    context.bind_user(uid)
    return uid


@router.get("/presets")
def policy_watch_presets(_user: str = Depends(_uid)) -> dict[str, Any]:
    cfg = policy_watch_config()
    presets_raw = cfg.get("presets") if isinstance(cfg.get("presets"), dict) else {}
    descriptions = {
        "gov_zhengce": "中国政府网最新政策栏目",
        "scio_news": "中国政府网发布栏目（含国新办新闻发布会）",
        "cctv": "新闻联播（结构化接口）",
        "macro": "宏观政策快照（结构化接口）",
    }
    presets = []
    for pid, meta in presets_raw.items():
        if not isinstance(meta, dict):
            continue
        item: dict[str, Any] = {
            "id": pid,
            "name": meta.get("name") or pid,
            "description": descriptions.get(pid, ""),
        }
        if meta.get("list_url"):
            item["list_url"] = meta["list_url"]
        presets.append(item)
    return {"presets": presets}


@router.get("/settings")
def policy_watch_settings_get(user_id: str = Depends(_uid)) -> dict[str, Any]:
    out = enrich_source_status(get_settings(user_id))
    out["llm_configured"] = bool(public_llm_settings(user_id).get("configured"))
    out["email_verified"] = peek_verified_email(user_id) is not None
    return out


@router.put("/settings")
def policy_watch_settings_put(
    body: dict[str, Any], user_id: str = Depends(_uid)
) -> dict[str, Any]:
    try:
        out = update_settings(user_id, body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out["llm_configured"] = bool(public_llm_settings(user_id).get("configured"))
    out["email_verified"] = peek_verified_email(user_id) is not None
    return out


@router.get("/items")
def policy_watch_items_get(
    user_id: str = Depends(_uid),
    filter: str = Query(default="all"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=50),
    page: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    return list_items(user_id, filter=filter, cursor=cursor, limit=limit, page=page)


@router.post("/items/{item_id}/read")
def policy_watch_item_read(
    item_id: str, user_id: str = Depends(_uid)
) -> dict[str, Any]:
    try:
        return mark_item_read(user_id, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
