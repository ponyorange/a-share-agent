"""Per-user advisor strategy (config overlay)."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from ..db import get_db
from .config_loader import default_config

# 系统模板用户：存量历史归档落在此，注册时再克隆给真实用户
SYSTEM_USER_ID = "__system__"

STRATEGY_EDITABLE_KEYS = (
    "buy_threshold",
    "add_threshold",
    "sell_threshold",
    "layer_weights",
    "market_scale",
    "weights",
    "high_vol_penalty",
    "high_vol_ann_threshold",
    "recommendations",
    "backtest",
    "disclaimer",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def default_strategy_doc(user_id: str) -> dict[str, Any]:
    now = _now()
    return {
        "user_id": user_id,
        "config": copy.deepcopy(default_config()),
        "source": "default",  # default | manual | agent
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "notes": "系统默认策略",
    }


def ensure_user_strategy(user_id: str) -> dict[str, Any]:
    """Ensure user has a strategy doc; create from system default if missing."""
    db = get_db()
    existing = db.user_strategies.find_one({"user_id": user_id}, {"_id": 0})
    if existing and existing.get("config"):
        return existing
    doc = default_strategy_doc(user_id)
    db.user_strategies.update_one(
        {"user_id": user_id},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "config": doc["config"],
                "source": doc["source"],
                "version": doc["version"],
                "created_at": doc["created_at"],
                "notes": doc["notes"],
            },
            "$set": {"updated_at": doc["updated_at"]},
        },
        upsert=True,
    )
    return db.user_strategies.find_one({"user_id": user_id}, {"_id": 0}) or doc


def get_user_strategy(user_id: str) -> dict[str, Any]:
    return ensure_user_strategy(user_id)


def get_user_config(user_id: str) -> dict[str, Any]:
    """Effective config dict for scoring / recommendations."""
    doc = ensure_user_strategy(user_id)
    cfg = doc.get("config") or {}
    # 与系统默认合并，避免缺字段
    return _deep_merge(default_config(), cfg)


def update_user_strategy(
    user_id: str,
    *,
    config_patch: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    source: str = "manual",
    notes: str | None = None,
) -> dict[str, Any]:
    """Update strategy. Prefer config_patch (deep merge) or full config replace."""
    db = get_db()
    ensure_user_strategy(user_id)
    current = db.user_strategies.find_one({"user_id": user_id}) or {}
    if config is not None:
        new_cfg = _deep_merge(default_config(), config)
    elif config_patch is not None:
        base = current.get("config") or default_config()
        new_cfg = _deep_merge(base, config_patch)
    else:
        raise ValueError("需要 config 或 config_patch")

    ver = int(current.get("version") or 1) + 1
    now = _now()
    updates: dict[str, Any] = {
        "config": new_cfg,
        "source": source if source in ("default", "manual", "agent") else "manual",
        "version": ver,
        "updated_at": now,
    }
    if notes is not None:
        updates["notes"] = notes
    db.user_strategies.update_one({"user_id": user_id}, {"$set": updates})
    return get_user_strategy(user_id)


def reset_user_strategy(user_id: str) -> dict[str, Any]:
    """Reset to system default config."""
    db = get_db()
    now = _now()
    current = db.user_strategies.find_one({"user_id": user_id}) or {}
    ver = int(current.get("version") or 1) + 1
    db.user_strategies.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "config": copy.deepcopy(default_config()),
                "source": "default",
                "version": ver,
                "updated_at": now,
                "notes": "已重置为系统默认",
            },
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return get_user_strategy(user_id)


def strategy_public_view(doc: dict[str, Any]) -> dict[str, Any]:
    """API-safe strategy payload."""
    cfg = _deep_merge(default_config(), doc.get("config") or {})
    return {
        "user_id": doc.get("user_id"),
        "source": doc.get("source") or "default",
        "version": doc.get("version") or 1,
        "notes": doc.get("notes"),
        "updated_at": (
            doc["updated_at"].isoformat()
            if hasattr(doc.get("updated_at"), "isoformat")
            else doc.get("updated_at")
        ),
        "config": {
            "buy_threshold": cfg.get("buy_threshold"),
            "add_threshold": cfg.get("add_threshold"),
            "sell_threshold": cfg.get("sell_threshold"),
            "layer_weights": cfg.get("layer_weights") or {},
            "market_scale": cfg.get("market_scale") or {},
            "weights": cfg.get("weights") or {},
            "high_vol_penalty": cfg.get("high_vol_penalty"),
            "high_vol_ann_threshold": cfg.get("high_vol_ann_threshold"),
            "recommendations": cfg.get("recommendations") or {},
            "backtest": cfg.get("backtest") or {},
            "disclaimer": cfg.get("disclaimer"),
        },
        "defaults": {
            "buy_threshold": default_config().get("buy_threshold"),
            "add_threshold": default_config().get("add_threshold"),
            "sell_threshold": default_config().get("sell_threshold"),
            "layer_weights": default_config().get("layer_weights") or {},
            "market_scale": default_config().get("market_scale") or {},
            "weights": default_config().get("weights") or {},
        },
    }


def ensure_strategies_for_all_users() -> dict[str, int]:
    """Backfill default strategy for every user lacking one."""
    db = get_db()
    created = 0
    for u in db.users.find({}, {"_id": 1}):
        uid = str(u["_id"])
        if not db.user_strategies.find_one({"user_id": uid}, {"_id": 1}):
            ensure_user_strategy(uid)
            created += 1
    return {"created": created}
