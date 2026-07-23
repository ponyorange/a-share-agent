"""RQ queue construction and generic committee job submission."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from redis import Redis
from rq import Queue
from rq.job import Job

from .redis_client import CommitteeRedisSettings, create_client, require_enabled
from .service import deterministic_job_id


def create_queue(
    settings: CommitteeRedisSettings | None = None,
    *,
    connection: Redis | Any | None = None,
) -> Queue:
    """Build the configured queue without issuing a Redis command."""
    resolved = settings if settings is not None else CommitteeRedisSettings.from_env()
    require_enabled(resolved)
    client = connection if connection is not None else create_client(resolved)
    return Queue(
        name=resolved.key("queue", resolved.queue_name),
        connection=client,
        default_timeout=resolved.job_timeout,
    )


def enqueue(
    func: Callable[..., Any] | str,
    *args: Any,
    settings: CommitteeRedisSettings | None = None,
    queue: Queue | None = None,
    **kwargs: Any,
) -> Job:
    """Submit a job with bounded execution and result retention."""
    resolved = settings if settings is not None else CommitteeRedisSettings.from_env()
    require_enabled(resolved)
    target_queue = queue if queue is not None else create_queue(resolved)
    return target_queue.enqueue_call(
        func=func,
        args=args,
        kwargs=kwargs,
        timeout=resolved.job_timeout,
        result_ttl=resolved.result_ttl,
        failure_ttl=resolved.failure_ttl,
        job_id=resolved.key("job", uuid4().hex),
    )


def enqueue_committee_run(
    user_id: str,
    run_id: str,
    idempotency_key: str,
    *,
    settings: CommitteeRedisSettings | None = None,
    queue: Queue | None = None,
    job_id: str | None = None,
) -> Job:
    """Enqueue one durable run with a stable RQ id and hard timeout."""
    resolved = settings if settings is not None else CommitteeRedisSettings.from_env()
    require_enabled(resolved)
    target_queue = queue if queue is not None else create_queue(resolved)
    from .tasks import rq_failure_callback, rq_stopped_callback

    return target_queue.enqueue_call(
        func="app.advisor.committee.tasks.execute_committee_job",
        args=(user_id, run_id),
        kwargs={},
        timeout=resolved.job_timeout,
        result_ttl=resolved.result_ttl,
        failure_ttl=resolved.failure_ttl,
        job_id=job_id or deterministic_job_id(user_id, idempotency_key),
        meta={"user_id": user_id, "run_id": run_id},
        on_failure=rq_failure_callback,
        on_stopped=rq_stopped_callback,
    )
