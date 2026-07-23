"""Migrate rec_snapshots to per-user and backfill strategies."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING

from ..db import get_db
from .user_strategy import (
    SYSTEM_USER_ID,
    ensure_strategies_for_all_users,
    ensure_user_strategy,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clone_system_snapshots_to_user(user_id: str) -> int:
    """Copy __system__ historical snapshots to a real user (skip existing dates)."""
    if user_id == SYSTEM_USER_ID:
        return 0
    db = get_db()
    cloned = 0
    for doc in db.rec_snapshots.find({"user_id": SYSTEM_USER_ID}):
        td = doc.get("trade_date")
        if not td:
            continue
        if db.rec_snapshots.find_one(
            {"user_id": user_id, "trade_date": td}, {"_id": 1}
        ):
            continue
        payload = {k: v for k, v in doc.items() if k != "_id"}
        payload["user_id"] = user_id
        payload["cloned_from"] = SYSTEM_USER_ID
        payload["updated_at"] = _now()
        db.rec_snapshots.insert_one(payload)
        cloned += 1
    return cloned


def migrate_rec_snapshots_to_users() -> dict[str, Any]:
    """One-shot: legacy trade_date-only docs → __system__ + clone to every user.

    Safe to re-run: skips already-keyed docs.
    """
    db = get_db()
    ensure_strategies_for_all_users()

    # 1) Promote legacy docs (no user_id) into __system__
    legacy = list(db.rec_snapshots.find({"user_id": {"$exists": False}}))
    legacy += list(db.rec_snapshots.find({"user_id": None}))
    seen_ids: set[Any] = set()
    unique_legacy: list[dict[str, Any]] = []
    for d in legacy:
        oid = d.get("_id")
        if oid in seen_ids:
            continue
        seen_ids.add(oid)
        unique_legacy.append(d)

    promoted = 0
    for doc in unique_legacy:
        td = doc.get("trade_date")
        if not td:
            continue
        payload = {
            k: v
            for k, v in doc.items()
            if k not in ("_id", "created_at")
        }
        payload["user_id"] = SYSTEM_USER_ID
        payload["updated_at"] = _now()
        db.rec_snapshots.update_one(
            {"user_id": SYSTEM_USER_ID, "trade_date": td},
            {
                "$set": payload,
                "$setOnInsert": {"created_at": doc.get("created_at") or _now()},
            },
            upsert=True,
        )
        db.rec_snapshots.delete_one({"_id": doc["_id"]})
        promoted += 1

    for doc in db.rec_snapshots.find({"user_id": ""}):
        td = doc.get("trade_date")
        if not td:
            continue
        payload = {
            k: v for k, v in doc.items() if k not in ("_id", "created_at")
        }
        payload["user_id"] = SYSTEM_USER_ID
        payload["updated_at"] = _now()
        db.rec_snapshots.update_one(
            {"user_id": SYSTEM_USER_ID, "trade_date": td},
            {
                "$set": payload,
                "$setOnInsert": {"created_at": doc.get("created_at") or _now()},
            },
            upsert=True,
        )
        db.rec_snapshots.delete_one({"_id": doc["_id"]})
        promoted += 1

    # 2) Clone __system__ → every real user
    user_ids = [str(u["_id"]) for u in db.users.find({}, {"_id": 1})]
    cloned_total = 0
    for uid in user_ids:
        ensure_user_strategy(uid)
        cloned_total += clone_system_snapshots_to_user(uid)

    return {
        "legacy_promoted": promoted,
        "users": len(user_ids),
        "snapshots_cloned": cloned_total,
        "system_dates": db.rec_snapshots.count_documents({"user_id": SYSTEM_USER_ID}),
    }


def ensure_rec_snapshot_indexes() -> None:
    """Drop old trade_date-only unique index; ensure (user_id, trade_date)."""
    db = get_db()
    for idx in db.rec_snapshots.list_indexes():
        name = idx.get("name") or ""
        key = list((idx.get("key") or {}).items())
        if key == [("trade_date", 1)] and idx.get("unique"):
            try:
                db.rec_snapshots.drop_index(name)
            except Exception:
                pass
    db.rec_snapshots.create_index(
        [("user_id", ASCENDING), ("trade_date", ASCENDING)],
        unique=True,
    )
    db.user_strategies.create_index([("user_id", ASCENDING)], unique=True)
