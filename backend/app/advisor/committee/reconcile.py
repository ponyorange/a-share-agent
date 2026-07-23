"""Watchdog reconciliation between durable runs and ephemeral RQ state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Callable

from rq.job import Job

from .models import RunStatus
from .repository import CommitteeRepository, VersionConflict


def classify_run_failure(
    *,
    error: BaseException | None,
    stopped: bool,
    cancel_requested: bool,
    checkpoint_exists: bool,
    resume_attempts: int,
    max_resume_attempts: int,
) -> str:
    if cancel_requested:
        return "cancelled"
    if not checkpoint_exists or resume_attempts >= max_resume_attempts:
        return "failed"
    error_name = type(error).__name__ if error is not None else ""
    deterministic_names = {
        "CommitteeConfigurationError",
        "DataValidationError",
        "ValidationError",
        "ApprovalRejected",
        "IllegalStatusTransition",
    }
    if isinstance(error, ValueError) or error_name in deterministic_names:
        return "failed"
    # RQ stop, timeout, worker loss and infrastructure errors are resumable.
    del stopped
    return "resume"


def _job_status(job: Any) -> str:
    value = job.get_status(refresh=True)
    return str(getattr(value, "value", value)).lower()


def reconcile_stale_runs(
    *,
    repository: CommitteeRepository,
    connection: Any,
    stale_seconds: int = 120,
    now: datetime | None = None,
    max_resume_attempts: int = 3,
    checkpoint_exists: Callable[[str, str], bool] | None = None,
    enqueue_resume: Callable[[str, str, int], Any] | None = None,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=stale_seconds)
    recovered: list[str] = []

    if checkpoint_exists is None:
        def checkpoint_exists(user_id: str, run_id: str) -> bool:
            try:
                from .checkpoint import create_checkpoint_saver
                from .execution import committee_thread_id
                from .redis_client import CommitteeRedisSettings

                settings = CommitteeRedisSettings.from_env()
                saver = create_checkpoint_saver(settings, client=connection)
                value = saver.get_tuple(
                    {
                        "configurable": {
                            "thread_id": committee_thread_id(user_id, run_id)
                        }
                    }
                )
                return value is not None
            except Exception:
                return False

    if enqueue_resume is None:
        def enqueue_resume(user_id: str, run_id: str, attempt: int) -> Any:
            from .jobs import create_queue
            from .redis_client import CommitteeRedisSettings
            from .tasks import rq_failure_callback, rq_stopped_callback

            settings = CommitteeRedisSettings.from_env()
            queue = create_queue(settings, connection=connection)
            # RQ rejects job ids that contain ":"; keep them hex-only.
            resume_job_id = (
                "committee-resume-"
                + hashlib.sha256(
                    f"{user_id}\0{run_id}\0{attempt}".encode()
                ).hexdigest()
            )
            delay = min(300, 5 * (2 ** (attempt - 1)))
            return queue.enqueue_in(
                timedelta(seconds=delay),
                "app.advisor.committee.tasks.execute_committee_job",
                user_id,
                run_id,
                job_id=resume_job_id,
                job_timeout=settings.job_timeout,
                result_ttl=settings.result_ttl,
                failure_ttl=settings.failure_ttl,
                meta={"user_id": user_id, "run_id": run_id},
                on_failure=rq_failure_callback,
                on_stopped=rq_stopped_callback,
            )

    for run in repository.list_stale_runs(heartbeat_before=cutoff):
        next_resume_at = getattr(run, "next_resume_at", None)
        if next_resume_at is not None and next_resume_at > now:
            continue
        heartbeat = run.job_heartbeat_at or run.updated_at
        if heartbeat > cutoff and (
            run.job_deadline_at is None or run.job_deadline_at > now
        ):
            continue
        try:
            job = Job.fetch(run.queue_job_id, connection=connection)
            status = _job_status(job)
        except Exception:
            job = None
            status = "missing"
        execution_heartbeat = max(
            value
            for value in (
                getattr(run, "execution_heartbeat_at", None),
                getattr(run, "job_heartbeat_at", None),
                run.updated_at,
            )
            if value is not None
        )
        execution_active = (
            status == "started"
            and bool(getattr(run, "execution_owner", None))
            and getattr(run, "execution_lease_expires_at", None) is not None
            and run.execution_lease_expires_at > now
            and execution_heartbeat > cutoff
            and run.job_deadline_at is not None
            and run.job_deadline_at > now
        )
        if execution_active:
            continue
        if status in {"queued", "deferred", "scheduled"} and (
            run.job_deadline_at is None or run.job_deadline_at > now
        ):
            continue
        try:
            latest = repository.get_run(run.user_id, run.run_id)
            if latest.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                continue
            has_checkpoint = checkpoint_exists(
                latest.user_id, latest.run_id
            )
            error_text = str(getattr(job, "exc_info", "") or "")
            deterministic_error = any(
                marker in error_text
                for marker in (
                    "ValidationError",
                    "ConfigurationError",
                    "DataValidationError",
                    "ApprovalRejected",
                )
            )
            disposition = classify_run_failure(
                error=(
                    ValueError("deterministic job failure")
                    if deterministic_error
                    else RuntimeError(status)
                ),
                stopped=status in {"stopped", "canceled", "cancelled"},
                cancel_requested=bool(latest.cancel_requested),
                checkpoint_exists=has_checkpoint,
                resume_attempts=int(
                    getattr(latest, "resume_attempts", 0)
                ),
                max_resume_attempts=max_resume_attempts,
            )
            if disposition == "cancelled":
                repository.request_cancel(
                    latest.user_id,
                    latest.run_id,
                    expected_version=latest.version,
                )
            elif disposition == "resume":
                previous_attempts = int(
                    getattr(latest, "resume_attempts", 0)
                )
                resume_attempt = previous_attempts + 1
                resumed_job = enqueue_resume(
                    latest.user_id,
                    latest.run_id,
                    resume_attempt,
                )
                delay = min(300, 5 * (2 ** (resume_attempt - 1)))
                fallback_job_id = "committee-resume-" + hashlib.sha256(
                    (
                        f"{latest.user_id}\0{latest.run_id}\0"
                        f"{resume_attempt}"
                    ).encode()
                ).hexdigest()
                job_id = str(
                    getattr(resumed_job, "id", None) or fallback_job_id
                )
                repository.record_resume_enqueued(
                    latest.user_id,
                    latest.run_id,
                    expected_resume_attempts=previous_attempts,
                    queue_job_id=job_id,
                    next_resume_at=now + timedelta(seconds=delay),
                    job_deadline_at=now + timedelta(seconds=stale_seconds * 5),
                )
                graph_attempt = int(getattr(latest, "attempt", 1))
                repository.append_outbox_event(
                    latest.user_id,
                    latest.run_id,
                    attempt=graph_attempt,
                    node="watchdog",
                    event_type="resume_enqueued",
                    event_key=(
                        f"attempt:{graph_attempt}:watchdog:"
                        f"resume:{resume_attempt}"
                    ),
                    payload={
                        "resume_attempt": resume_attempt,
                        "backoff_seconds": delay,
                    },
                )
            else:
                repository.transition_status(
                    latest.user_id,
                    latest.run_id,
                    expected_version=latest.version,
                    new_status=RunStatus.FAILED,
                    error_code="stale_rq_job",
                    error_message=f"RQ job is stale or terminal: {status}",
                )
            recovered.append(latest.run_id)
        except VersionConflict:
            continue
    return recovered
