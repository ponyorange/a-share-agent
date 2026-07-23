"""Background job for recommendations universe refresh (in-process thread)."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from ..db import get_db
from . import context
from .service import iter_recommendations_refresh_events

ACTIVE_STATUSES = ("queued", "running")
_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _col():
    return get_db().rec_refresh_jobs


def _public(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    return {
        "job_id": doc.get("job_id"),
        "user_id": doc.get("user_id"),
        "trade_date": doc.get("trade_date"),
        "status": doc.get("status"),
        "top": doc.get("top"),
        "board": doc.get("board"),
        "progress": doc.get("progress") or {},
        "error": doc.get("error"),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "finished_at": _iso(doc.get("finished_at")),
    }


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def get_job(user_id: str, job_id: str) -> dict[str, Any] | None:
    doc = _col().find_one({"user_id": user_id, "job_id": job_id}, {"_id": 0})
    return _public(doc)


def find_active_job(user_id: str, trade_date: str | None = None) -> dict[str, Any] | None:
    q: dict[str, Any] = {
        "user_id": user_id,
        "status": {"$in": list(ACTIVE_STATUSES)},
    }
    if trade_date:
        q["trade_date"] = trade_date
    doc = _col().find_one(q, {"_id": 0}, sort=[("created_at", -1)])
    return _public(doc)


def _update(job_id: str, user_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    _col().update_one(
        {"user_id": user_id, "job_id": job_id},
        {"$set": fields},
    )


def _run_job(job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])
    user_id = str(job["user_id"])
    try:
        context.bind_user(user_id)
        _update(job_id, user_id, status="running")
        for ev in iter_recommendations_refresh_events(
            top=int(job.get("top") or 10),
            as_of=job.get("as_of") or job.get("trade_date"),
            board=None if job.get("board") in (None, "", "all") else job.get("board"),
            user_id=user_id,
            persist=bool(job.get("persist", True)),
            trade_date=job.get("trade_date"),
        ):
            et = ev.get("event")
            data = ev.get("data") or {}
            if et == "progress":
                _update(
                    job_id,
                    user_id,
                    progress={
                        "phase": data.get("phase"),
                        "done": data.get("done"),
                        "total": data.get("total"),
                        "message": data.get("message"),
                        "symbol": data.get("symbol"),
                        "name": data.get("name"),
                        "step": data.get("step"),
                        "board": data.get("board"),
                    },
                )
            elif et == "meta":
                _update(
                    job_id,
                    user_id,
                    progress={
                        "phase": data.get("phase") or "universe",
                        "done": 0,
                        "total": 0,
                        "message": "开始刷新",
                    },
                )
            elif et == "error":
                _update(
                    job_id,
                    user_id,
                    status="failed",
                    error=str(data.get("detail") or "刷新失败"),
                    finished_at=_now(),
                )
                return
            elif et == "done":
                _update(
                    job_id,
                    user_id,
                    status="completed",
                    progress={
                        "phase": "done",
                        "done": 1,
                        "total": 1,
                        "message": "刷新完成",
                    },
                    error=None,
                    finished_at=_now(),
                )
                return
        _update(
            job_id,
            user_id,
            status="failed",
            error="任务异常结束（无 done 事件）",
            finished_at=_now(),
        )
    except Exception as exc:
        _update(
            job_id,
            user_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=_now(),
        )
    finally:
        with _lock:
            _threads.pop(job_id, None)


def start_refresh_job(
    user_id: str,
    *,
    trade_date: str,
    top: int = 10,
    board: str = "all",
    as_of: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Create or reuse an active job and ensure a worker thread is running."""
    existing = find_active_job(user_id, trade_date)
    if existing:
        job_id = str(existing["job_id"])
        with _lock:
            th = _threads.get(job_id)
            if th is None or not th.is_alive():
                # Process restarted or thread lost — mark failed so user can retry
                _update(
                    job_id,
                    user_id,
                    status="failed",
                    error="服务重启或后台线程已中断，请重新点击刷新",
                    finished_at=_now(),
                )
            else:
                return existing

    now = _now()
    job_id = str(uuid.uuid4())
    doc = {
        "job_id": job_id,
        "user_id": user_id,
        "trade_date": trade_date,
        "as_of": as_of or trade_date,
        "top": max(1, min(int(top), 50)),
        "board": board if board in ("etf", "hs", "star", "all") else "all",
        "persist": persist,
        "status": "queued",
        "progress": {
            "phase": "queued",
            "done": 0,
            "total": 0,
            "message": "排队中",
        },
        "error": None,
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }
    _col().insert_one(doc)
    doc.pop("_id", None)

    th = threading.Thread(
        target=_run_job,
        args=(doc,),
        name=f"rec-refresh-{job_id[:8]}",
        daemon=True,
    )
    with _lock:
        _threads[job_id] = th
    th.start()
    return _public(doc) or doc


def iter_job_sse_events(
    user_id: str,
    job_id: str,
    *,
    poll_seconds: float = 0.4,
) -> Iterator[dict[str, Any]]:
    """Poll Mongo job doc and yield progress/meta/done/error for SSE clients."""
    last_key: str | None = None
    seen_terminal = False
    while True:
        doc = _col().find_one({"user_id": user_id, "job_id": job_id}, {"_id": 0})
        if not doc:
            yield {"event": "error", "data": {"detail": "任务不存在"}}
            return
        status = doc.get("status")
        progress = doc.get("progress") or {}
        key = f"{status}|{progress.get('phase')}|{progress.get('done')}|{progress.get('total')}|{progress.get('message')}|{progress.get('symbol')}|{doc.get('error')}"
        if key != last_key:
            last_key = key
            if status == "queued":
                yield {
                    "event": "meta",
                    "data": {
                        "job_id": job_id,
                        "trade_date": doc.get("trade_date"),
                        "status": status,
                        "phase": "queued",
                    },
                }
            elif status in ACTIVE_STATUSES:
                yield {
                    "event": "progress",
                    "data": {
                        "job_id": job_id,
                        "status": status,
                        **progress,
                    },
                }
            elif status == "completed":
                yield {
                    "event": "done",
                    "data": {
                        "job_id": job_id,
                        "status": status,
                        "trade_date": doc.get("trade_date"),
                        "progress": progress,
                    },
                }
                return
            elif status == "failed":
                yield {
                    "event": "error",
                    "data": {
                        "job_id": job_id,
                        "detail": doc.get("error") or "刷新失败",
                        "status": status,
                    },
                }
                return
        if status not in ACTIVE_STATUSES:
            if seen_terminal:
                return
            seen_terminal = True
        time.sleep(poll_seconds)
