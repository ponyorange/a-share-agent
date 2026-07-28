"""Mongo persistence for agent monitor jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from bson import ObjectId
from pymongo import ReturnDocument
from pydantic import ValidationError

from ...db import get_db
from ...kline import normalize_symbol
from .models import CreateJobBody, rule_to_dict

JOBS_MAX_PER_USER = 20
SYMBOLS_MAX = 50
DEFAULT_COOLDOWN_SEC = 1800


def require_verified_email(user_id: str) -> str:
    try:
        oid = ObjectId(user_id)
    except Exception as exc:
        raise ValueError("请先在个人资料绑定并验证邮箱") from exc
    user = get_db().users.find_one({"_id": oid})
    if not user:
        raise ValueError("请先在个人资料绑定并验证邮箱")
    email = user.get("email")
    if not isinstance(email, str) or not user.get("email_verified_at"):
        raise ValueError("请先在个人资料绑定并验证邮箱")
    return email.strip().lower()


def _serialize(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    out = dict(doc)
    oid = out.pop("_id", None)
    if oid is not None:
        out["id"] = str(oid)
    for key in (
        "created_at",
        "updated_at",
        "last_run_at",
        "last_alert_at",
        "last_llm_at",
    ):
        val = out.get(key)
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    return out


def list_jobs(user_id: str) -> list[dict[str, Any]]:
    cur = (
        get_db()
        .agent_monitor_jobs.find({"user_id": user_id})
        .sort("updated_at", -1)
    )
    return [_serialize(d) for d in cur if d]  # type: ignore[misc]


def get_job(user_id: str, job_id: str) -> dict[str, Any] | None:
    try:
        oid = ObjectId(job_id)
    except Exception:
        return None
    doc = get_db().agent_monitor_jobs.find_one({"_id": oid, "user_id": user_id})
    return _serialize(doc)


def find_jobs_by_title(user_id: str, title: str) -> list[dict[str, Any]]:
    q = title.strip()
    if not q:
        return []
    cur = get_db().agent_monitor_jobs.find({"user_id": user_id, "title": q})
    return [_serialize(d) for d in cur if d]  # type: ignore[misc]


def create_job(user_id: str, body: CreateJobBody | dict[str, Any]) -> dict[str, Any]:
    if isinstance(body, dict):
        try:
            body = CreateJobBody.model_validate(body)
        except ValidationError as exc:
            msgs = []
            for err in exc.errors():
                loc = ".".join(str(x) for x in err.get("loc") or ())
                msg = str(err.get("msg") or "参数无效")
                msgs.append(f"{loc}: {msg}" if loc else msg)
            raise ValueError("; ".join(msgs) or "参数无效") from exc

    email = require_verified_email(user_id)
    db = get_db()
    count = db.agent_monitor_jobs.count_documents({"user_id": user_id})
    if count >= JOBS_MAX_PER_USER:
        raise ValueError(f"定时任务已达上限 {JOBS_MAX_PER_USER} 条")

    symbols: list[str] = []
    if body.scope == "symbols":
        seen: set[str] = set()
        for raw in body.symbols:
            try:
                sym = normalize_symbol(str(raw))
            except ValueError:
                continue
            if sym in seen:
                continue
            seen.add(sym)
            symbols.append(sym)
            if len(symbols) >= SYMBOLS_MAX:
                break
        if not symbols:
            raise ValueError("指定代码列表不能为空")

    rules = []
    for r in body.rules:
        rid = (r.id or uuid4().hex[:8]).strip() or uuid4().hex[:8]
        rules.append(rule_to_dict(r, rid))

    llm_enabled = bool(body.llm_enabled)
    if llm_enabled:
        from ..llm_settings import resolve_llm_credentials

        try:
            resolve_llm_credentials(user_id)
        except ValueError as exc:
            raise ValueError(
                "请先在 Agent 设置中配置 DeepSeek API Key"
            ) from exc

    now = datetime.now(timezone.utc)
    cooldown = (
        int(body.cooldown_sec)
        if body.cooldown_sec is not None and body.cooldown_sec > 0
        else DEFAULT_COOLDOWN_SEC
    )
    try:
        llm_interval = (
            int(body.llm_interval_sec)
            if body.llm_interval_sec is not None and body.llm_interval_sec > 0
            else 900
        )
    except (TypeError, ValueError):
        llm_interval = 900
    try:
        llm_anom = (
            float(body.llm_anomaly_abs_chg)
            if body.llm_anomaly_abs_chg is not None
            else 0.03
        )
    except (TypeError, ValueError):
        llm_anom = 0.03
    if llm_anom <= 0:
        llm_anom = 0.03

    doc = {
        "user_id": user_id,
        "title": body.title,
        "status": "running",
        "scope": body.scope,
        "symbols": symbols,
        "rules": rules,
        "note": (body.note or None),
        "notify_email": email,
        "cooldown_sec": cooldown,
        "llm_enabled": llm_enabled,
        "llm_interval_sec": llm_interval,
        "llm_anomaly_abs_chg": llm_anom,
        "knowledge_ids": list(body.knowledge_ids or []),
        "created_at": now,
        "updated_at": now,
        "last_run_at": None,
        "last_alert_at": None,
        "last_llm_at": None,
        "llm_symbol_baselines": {},
        "alert_cooldowns": {},
        "last_error": None,
        "last_llm_error": None,
    }
    res = db.agent_monitor_jobs.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _serialize(doc)  # type: ignore[return-value]


def pause_job(user_id: str, job_id: str) -> dict[str, Any]:
    return _set_status(user_id, job_id, "paused")


def resume_job(user_id: str, job_id: str) -> dict[str, Any]:
    return _set_status(user_id, job_id, "running")


def _set_status(user_id: str, job_id: str, status: str) -> dict[str, Any]:
    try:
        oid = ObjectId(job_id)
    except Exception as exc:
        raise ValueError("任务不存在") from exc
    now = datetime.now(timezone.utc)
    res = get_db().agent_monitor_jobs.find_one_and_update(
        {"_id": oid, "user_id": user_id},
        {"$set": {"status": status, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not res:
        raise ValueError("任务不存在")
    return _serialize(res)  # type: ignore[return-value]


def delete_job(user_id: str, job_id: str) -> None:
    try:
        oid = ObjectId(job_id)
    except Exception as exc:
        raise ValueError("任务不存在") from exc
    res = get_db().agent_monitor_jobs.delete_one({"_id": oid, "user_id": user_id})
    if res.deleted_count == 0:
        raise ValueError("任务不存在")


def resolve_symbols(job: dict[str, Any]) -> list[str]:
    scope = job.get("scope")
    if scope == "watchlist":
        from ..watchlist import load_watchlist

        items = load_watchlist(job["user_id"]).get("items") or []
        return [str(x.get("symbol")) for x in items if x.get("symbol")]
    if scope == "portfolio":
        from ..portfolio import load_portfolio

        positions = load_portfolio(job["user_id"]).get("positions") or []
        return [str(x.get("symbol")) for x in positions if x.get("symbol")]
    return list(job.get("symbols") or [])


def list_running_jobs() -> list[dict[str, Any]]:
    cur = get_db().agent_monitor_jobs.find({"status": "running"})
    out = []
    for doc in cur:
        s = _serialize(doc)
        if s:
            # keep user_id for resolve
            s["user_id"] = doc.get("user_id")
            out.append(s)
    return out


def touch_job_run(job_id: str, **fields: Any) -> None:
    try:
        oid = ObjectId(job_id)
    except Exception:
        return
    payload = dict(fields)
    payload["updated_at"] = datetime.now(timezone.utc)
    get_db().agent_monitor_jobs.update_one({"_id": oid}, {"$set": payload})
