"""Application services shared by committee HTTP routes and RQ jobs."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable


def deterministic_job_id(user_id: str, idempotency_key: str) -> str:
    if not user_id or not idempotency_key:
        raise ValueError("user_id and idempotency_key are required")
    digest = hashlib.sha256(
        f"{user_id}\0{idempotency_key}".encode()
    ).hexdigest()
    return f"committee-run-{digest}"


def _event_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {
        "event_id": value.event_id,
        "event_type": value.event_type,
        "payload": value.payload,
        "created_at": value.created_at,
    }


def _stream_id(value: str) -> tuple[int, int]:
    try:
        first, second = value.split("-", 1)
        return int(first), int(second)
    except (AttributeError, TypeError, ValueError):
        return (0, 0)


def merged_event_history(
    mongo_events: Iterable[Any],
    redis_events: Iterable[Any],
    last_event_id: str = "0-0",
) -> list[dict[str, Any]]:
    """Merge durable and live events while preserving resumable stream IDs."""
    after = _stream_id(last_event_id or "0-0")
    by_id: dict[str, dict[str, Any]] = {}
    for raw in (*tuple(mongo_events), *tuple(redis_events)):
        event = _event_dict(raw)
        event_id = str(event.get("event_id") or "")
        if _stream_id(event_id) <= after:
            continue
        by_id[event_id] = event
    return [
        by_id[event_id]
        for event_id in sorted(by_id, key=_stream_id)
    ]
