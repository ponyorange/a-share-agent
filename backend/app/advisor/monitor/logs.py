"""Append-only monitor job logs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from ...db import get_db

LOG_LIMIT_DEFAULT = 100
LOG_LIMIT_MAX = 200


def append_job_log(
    user_id: str,
    job_id: str,
    *,
    level: str,
    event: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    get_db().agent_monitor_job_logs.insert_one(
        {
            "user_id": user_id,
            "job_id": str(job_id),
            "ts": datetime.now(timezone.utc),
            "level": level,
            "event": event,
            "message": (message or "")[:500],
            "detail": detail,
        }
    )


def list_job_logs(
    user_id: str,
    job_id: str,
    *,
    after_ts: str | None = None,
    limit: int = LOG_LIMIT_DEFAULT,
) -> list[dict[str, Any]]:
    n = max(1, min(int(limit), LOG_LIMIT_MAX))
    q: dict[str, Any] = {"user_id": user_id, "job_id": str(job_id)}
    if after_ts:
        try:
            text = after_ts.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            q["ts"] = {"$gt": dt.astimezone(timezone.utc)}
        except ValueError:
            pass
    cur = (
        get_db()
        .agent_monitor_job_logs.find(q)
        .sort("ts", -1)
        .limit(n)
    )
    out: list[dict[str, Any]] = []
    for doc in cur:
        ts = doc.get("ts")
        out.append(
            {
                "id": str(doc.get("_id")),
                "job_id": doc.get("job_id"),
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else ts,
                "level": doc.get("level"),
                "event": doc.get("event"),
                "message": doc.get("message"),
                "detail": doc.get("detail"),
            }
        )
    out.reverse()
    return out


def delete_job_logs(user_id: str, job_id: str) -> None:
    get_db().agent_monitor_job_logs.delete_many(
        {"user_id": user_id, "job_id": str(job_id)}
    )
