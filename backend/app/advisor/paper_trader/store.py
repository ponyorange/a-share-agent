"""Mongo persistence for paper trader sessions and decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from bson import ObjectId
from pymongo import ReturnDocument

from ...db import get_db
from .defaults import default_paper_trader_config
from .models import PatchBody, StartBody, merge_risk


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(val: Any) -> Any:
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    return val


def _public(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    out = dict(doc)
    oid = out.pop("_id", None)
    if oid is not None:
        out["id"] = str(oid)
    for key in (
        "next_run_at",
        "last_run_at",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    ):
        if key in out:
            out[key] = _iso(out[key])
    return out


def _empty_stats() -> dict[str, int]:
    return {
        "trades": 0,
        "buys": 0,
        "sells": 0,
        "blocked": 0,
        "llm_calls": 0,
        "rounds": 0,
    }


def _peek_verified_email(user_id: str) -> str | None:
    try:
        from ..monitor.store import require_verified_email

        return require_verified_email(user_id)
    except Exception:
        return None


def get_session(user_id: str) -> dict[str, Any] | None:
    doc = get_db().paper_trader_sessions.find_one({"user_id": user_id})
    return _public(doc)


def start_session(user_id: str, body: StartBody | None = None) -> dict[str, Any]:
    cfg = default_paper_trader_config()
    now = _now()
    existing = get_db().paper_trader_sessions.find_one({"user_id": user_id})
    mode = (body.mode if body and body.mode else None) or (
        (existing or {}).get("mode") or "signal_first"
    )
    interval = (
        int(body.interval_sec)
        if body and body.interval_sec is not None
        else int((existing or {}).get("interval_sec") or cfg["interval_sec"])
    )
    interval = max(300, min(900, interval))
    risk = merge_risk(
        (existing or {}).get("risk") or cfg.get("risk") or {},
        body.risk if body else None,
    )
    email = _peek_verified_email(user_id)
    fields: dict[str, Any] = {
        "user_id": user_id,
        "status": "running",
        "mode": mode if mode in ("signal_first", "llm_first") else "signal_first",
        "interval_sec": interval,
        "risk": risk,
        "candidate": {"sources": ["recommendations", "watchlist"]},
        "notify_email": email,
        "next_run_at": now,
        "last_error": None,
        "halt_reason": None,
        "updated_at": now,
        "consecutive_zero_fill": 0,
        "consecutive_llm_fail": 0,
    }
    if existing is None:
        fields.update(
            {
                "day_anchor": None,
                "equity_day_open": None,
                "stats_today": _empty_stats(),
                "day_end_sent_for": None,
                "created_at": now,
            }
        )
        get_db().paper_trader_sessions.insert_one(fields)
    else:
        get_db().paper_trader_sessions.update_one(
            {"user_id": user_id},
            {"$set": fields},
        )
    out = get_session(user_id)
    assert out is not None
    return out


def pause_session(user_id: str) -> dict[str, Any]:
    doc = touch_session(user_id, status="paused", next_run_at=None)
    if not doc:
        raise ValueError("paper trader session not found")
    return doc


def stop_session(user_id: str) -> dict[str, Any]:
    doc = touch_session(
        user_id,
        status="stopped",
        next_run_at=None,
        halt_reason=None,
    )
    if not doc:
        raise ValueError("paper trader session not found")
    return doc


def resume_session(
    user_id: str, *, confirm_halt_resume: bool = False
) -> dict[str, Any]:
    cur = get_db().paper_trader_sessions.find_one({"user_id": user_id})
    if not cur:
        raise ValueError("paper trader session not found")
    status = str(cur.get("status") or "")
    if status == "halted" and not confirm_halt_resume:
        raise ValueError("halted session requires confirm_halt_resume")
    if status not in ("paused", "halted", "stopped"):
        return _public(cur)  # type: ignore[return-value]
    now = _now()
    return touch_session(
        user_id,
        status="running",
        next_run_at=now,
        halt_reason=None,
        last_error=None,
        consecutive_llm_fail=0,
    )


def patch_session(user_id: str, body: PatchBody) -> dict[str, Any]:
    cur = get_db().paper_trader_sessions.find_one({"user_id": user_id})
    if not cur:
        raise ValueError("paper trader session not found")
    fields: dict[str, Any] = {"updated_at": _now()}
    if body.mode is not None:
        fields["mode"] = body.mode
    if body.interval_sec is not None:
        fields["interval_sec"] = max(300, min(900, int(body.interval_sec)))
    if body.risk is not None:
        fields["risk"] = merge_risk(cur.get("risk") or {}, body.risk)
    return touch_session(user_id, **fields)


def touch_session(user_id: str, **fields: Any) -> dict[str, Any]:
    if "updated_at" not in fields:
        fields = {**fields, "updated_at": _now()}
    doc = get_db().paper_trader_sessions.find_one_and_update(
        {"user_id": user_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    pub = _public(doc)
    if pub is None:
        raise ValueError("paper trader session not found")
    return pub


def list_due_sessions(now: datetime, *, limit: int) -> list[dict[str, Any]]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cur = (
        get_db()
        .paper_trader_sessions.find(
            {
                "status": "running",
                "next_run_at": {"$lte": now},
            }
        )
        .sort("next_run_at", 1)
        .limit(max(1, int(limit)))
    )
    return [_public(d) for d in cur if d]  # type: ignore[misc]


def insert_decision(doc: dict[str, Any]) -> dict[str, Any]:
    body = dict(doc)
    body.setdefault("run_id", str(uuid4()))
    if "_id" not in body:
        body["_id"] = ObjectId()
    get_db().paper_trader_decisions.insert_one(body)
    pub = _public(body)
    assert pub is not None
    return pub


def list_decisions(
    user_id: str, *, page: int = 1, page_size: int = 20
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    coll = get_db().paper_trader_decisions
    q = {"user_id": user_id}
    total = coll.count_documents(q)
    skip = (page - 1) * page_size
    rows = list(
        coll.find(q).sort("started_at", -1).skip(skip).limit(page_size)
    )
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [_public(r) for r in rows],
    }


def get_decision(user_id: str, decision_id: str) -> dict[str, Any] | None:
    try:
        oid = ObjectId(decision_id)
    except Exception:
        return None
    doc = get_db().paper_trader_decisions.find_one(
        {"_id": oid, "user_id": user_id}
    )
    return _public(doc)
