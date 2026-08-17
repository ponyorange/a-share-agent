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
from .logs import append_job_log, delete_job_logs
from .models import CreateJobBody, rule_to_dict
from .schedule import (
    DEFAULT_WATCH_END,
    DEFAULT_WATCH_START,
    compute_next_run_at,
    compute_watch_end_at,
    ensure_utc,
    format_shanghai,
    in_watch_window,
)

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


def _iso_utc(val: datetime) -> str:
    """Mongo returns naive UTC; always emit an offset so browsers don't treat as local."""
    if val.tzinfo is None:
        aware = val.replace(tzinfo=timezone.utc)
    else:
        aware = val.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def _serialize(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    out = normalize_legacy_job(dict(doc))
    oid = out.pop("_id", None)
    if oid is not None:
        out["id"] = str(oid)
    for key in (
        "created_at",
        "updated_at",
        "last_run_at",
        "last_alert_at",
        "last_llm_at",
        "next_run_at",
        "end_at",
        "started_at",
        "completed_at",
    ):
        val = out.get(key)
        if isinstance(val, datetime):
            out[key] = _iso_utc(val)
    return out


def normalize_legacy_job(doc: dict[str, Any]) -> dict[str, Any]:
    """Fill schedule defaults for jobs created before schedule enhancement."""
    out = dict(doc)
    legacy = "kind" not in doc
    out.setdefault("kind", "watch")
    out.setdefault("repeat", "recurring")
    out.setdefault("calendar", "trading_days")
    out.setdefault("tz", "Asia/Shanghai")
    out.setdefault("end_time", DEFAULT_WATCH_END)
    if "run_time" not in out:
        out["run_time"] = DEFAULT_WATCH_START if legacy else None
    out.setdefault("anchor_date", None)
    out.setdefault("prompt", None)
    status = out.get("status")
    if legacy:
        # Pre-schedule jobs were always treated as active watch.
        if status is None:
            out["status"] = "running"
    elif status is None:
        out["status"] = "scheduled"
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


def create_job(
    user_id: str,
    body: CreateJobBody | dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
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
    if llm_enabled or body.kind == "run_at":
        from ..llm_settings import resolve_llm_credentials

        try:
            resolve_llm_credentials(user_id, "monitor")
        except ValueError as exc:
            raise ValueError("请先在模型配置中填写 API Key") from exc

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
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

    kind = body.kind
    repeat = body.repeat
    calendar = body.calendar
    end_time = body.end_time or DEFAULT_WATCH_END
    run_time = body.run_time
    if kind == "watch" and not run_time:
        run_time = DEFAULT_WATCH_START

    schedule_doc = {
        "kind": kind,
        "repeat": repeat,
        "calendar": calendar,
        "tz": "Asia/Shanghai",
        "anchor_date": body.anchor_date,
        "run_time": run_time,
        "end_time": end_time,
        "prompt": (body.prompt or None),
        "status": "scheduled",
        "next_run_at": None,
        "end_at": None,
        "started_at": None,
        "completed_at": None,
    }
    nxt = compute_next_run_at(schedule_doc, now=now)
    schedule_doc["next_run_at"] = ensure_utc(nxt)
    if kind == "watch" and repeat == "once" and body.anchor_date:
        schedule_doc["end_at"] = ensure_utc(
            compute_watch_end_at(body.anchor_date, end_time)
        )
    if nxt is None and kind == "run_at" and repeat == "once":
        raise ValueError("定点时间已过，请换用未来时间或改成重复任务")
    # 盘中创建盯盘：立即进入 running（与 resume 一致），避免 next_run 被算到下一交易日
    if kind == "watch" and in_watch_window(schedule_doc, now=now):
        schedule_doc["status"] = "running"
        schedule_doc["started_at"] = now
        schedule_doc["next_run_at"] = None

    doc = {
        "user_id": user_id,
        "title": body.title,
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
        **schedule_doc,
    }
    res = db.agent_monitor_jobs.insert_one(doc)
    doc["_id"] = res.inserted_id
    if doc.get("status") == "running":
        create_msg = f"已创建（{kind}/{repeat}），盘中已激活盯盘"
    else:
        create_msg = f"已创建（{kind}/{repeat}），下次 {format_shanghai(doc.get('next_run_at'))}"
    append_job_log(
        user_id,
        str(res.inserted_id),
        level="info",
        event="created",
        message=create_msg,
    )
    return _serialize(doc)  # type: ignore[return-value]


def pause_job(user_id: str, job_id: str) -> dict[str, Any]:
    out = _set_status(user_id, job_id, "paused")
    append_job_log(user_id, job_id, level="info", event="paused", message="已暂停")
    return out


def resume_job(user_id: str, job_id: str) -> dict[str, Any]:
    try:
        oid = ObjectId(job_id)
    except Exception as exc:
        raise ValueError("任务不存在") from exc
    now = datetime.now(timezone.utc)
    doc = get_db().agent_monitor_jobs.find_one({"_id": oid, "user_id": user_id})
    if not doc:
        raise ValueError("任务不存在")
    norm = normalize_legacy_job(doc)
    nxt = compute_next_run_at(norm, now=now)
    status = "scheduled"
    started = None
    next_run = ensure_utc(nxt)
    if norm.get("kind") == "watch":
        # 仅窗口内立即 running；过期 due 不在盘后误激活（交给 activate 的 missed 逻辑）
        if in_watch_window(norm, now=now):
            status = "running"
            started = now
            next_run = None
    res = get_db().agent_monitor_jobs.find_one_and_update(
        {"_id": oid, "user_id": user_id},
        {
            "$set": {
                "status": status,
                "next_run_at": next_run,
                "started_at": started,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    append_job_log(user_id, job_id, level="info", event="resumed", message=f"已恢复 → {status}")
    return _serialize(res)  # type: ignore[return-value]


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
    delete_job_logs(user_id, job_id)


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
    """Watch jobs currently in running status (for intraday evaluation)."""
    cur = get_db().agent_monitor_jobs.find({"status": "running"})
    out = []
    for doc in cur:
        kind = doc.get("kind") or "watch"
        if kind != "watch":
            continue
        s = _serialize(doc)
        if s:
            s["user_id"] = doc.get("user_id")
            out.append(s)
    return out


def list_due_scheduled_jobs(now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    # Fake/simple find only supports equality; filter next_run_at in Python.
    cur = get_db().agent_monitor_jobs.find({"status": "scheduled"})
    out = []
    for doc in cur:
        nxt = doc.get("next_run_at")
        if nxt is None:
            continue
        if isinstance(nxt, datetime):
            nxt_utc = ensure_utc(nxt)
        else:
            continue
        if nxt_utc is None or nxt_utc > current:
            continue
        s = _serialize(doc)
        if s:
            s["user_id"] = doc.get("user_id")
            s["_raw"] = doc
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
