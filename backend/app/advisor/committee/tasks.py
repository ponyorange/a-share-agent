"""RQ task entrypoint for recoverable committee graph execution."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
import threading
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from rq import get_current_job

from ..config_loader import reload_config
from .agents import ChatModelRoleRunner, RoleAgentExecutor
from .checkpoint import initialize_checkpoint_saver
from .chat_stream import ChatStreamEvent
from .dependencies import create_production_dependencies
from .execution import committee_thread_id, create_committee_invoker
from .models import RunStatus
from .redis_client import CommitteeRedisSettings
from .repository import (
    CommitteeRepository,
    IllegalStatusTransition,
    VersionConflict,
)
from .runtime import CommitteeRuntime
from .snapshot import SnapshotBuilder, default_collector_specs


def _safe_error_message(error: BaseException | None) -> str:
    if error is None:
        return "RQ job failed"
    message = str(error) or type(error).__name__
    secret = os.getenv("REDIS_PASSWORD") or ""
    if secret:
        for value in {secret, quote(secret, safe=""), quote(secret, safe="/")}:
            if value:
                message = message.replace(value, "***")
    return message[:1000]


def canonical_event_key(
    run_id: str,
    attempt: int,
    node: str,
    event_type: str,
    payload: Any,
    *,
    semantic_id: str | None = None,
) -> str:
    logical_node = str(node or "unknown")
    if logical_node == "analyst_fan_in":
        logical_node = "fan_in"
    role = str(
        (payload or {}).get("role")
        if isinstance(payload, dict)
        else ""
    )
    round_value = (
        (payload or {}).get("round")
        if isinstance(payload, dict)
        else None
    )
    semantic_node = ""
    if semantic_id and semantic_id.startswith(f"{run_id}:"):
        suffix = semantic_id[len(run_id) + 1 :]
        marker = f":{event_type}"
        if suffix.endswith(marker):
            semantic_node = suffix[: -len(marker)]
    candidate = semantic_node or logical_node
    if candidate == "analyst_fan_in":
        candidate = "fan_in"
    if candidate.startswith(("bull:", "bear:")):
        candidate_role, candidate_round = candidate.split(":", 1)
        role = role or candidate_role
        round_value = round_value or candidate_round
    elif candidate in {"bull", "bear"}:
        role = role or candidate
    logical_node = (
        f"{role}:{round_value}"
        if role in {"bull", "bear"} and round_value is not None
        else role
        if role in {"bull", "bear"}
        else candidate
    )
    stable = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(stable.encode()).hexdigest()[:24]
    return (
        f"run:{run_id}:attempt:{attempt}:node:{node}:"
        f"event:{event_type}:{digest}"
    )


def _recovery_payload(
    user_id: str,
    run_id: str,
    initial_input: dict[str, Any],
    *,
    checkpoint_exists: bool,
    attempt: int,
) -> dict[str, Any] | None:
    if checkpoint_exists:
        return None
    return {
        "user_id": user_id,
        "run_id": run_id,
        **dict(initial_input),
        "attempt": attempt,
    }


def _create_role_executor(
    config: dict[str, Any],
    runtime: CommitteeRuntime,
    user_id: str,
    run_id: str,
) -> RoleAgentExecutor:
    async def stream_sink(event: ChatStreamEvent) -> None:
        try:
            await asyncio.to_thread(
                runtime.append_ephemeral_event,
                user_id,
                run_id,
                event.event_type,
                event.payload,
            )
        except Exception:
            return

    return RoleAgentExecutor(
        ChatModelRoleRunner(config, stream_sink=stream_sink)
    )


def _terminalize_rq_job(
    job: Any,
    *,
    connection: Any,
    repository: CommitteeRepository | None,
    stopped: bool,
    error: BaseException | None,
) -> None:
    resolved = repository or CommitteeRepository.from_default_database()
    user_id = str(job.meta["user_id"])
    run_id = str(job.meta["run_id"])
    run = resolved.get_run(user_id, run_id)
    if run.status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        return
    try:
        from .reconcile import reconcile_stale_runs

        previous_resumes = int(getattr(run, "resume_attempts", 0))
        reconcile_stale_runs(
            repository=resolved,
            connection=connection,
            stale_seconds=0,
        )
        run = resolved.get_run(user_id, run_id)
        if (
            run.status
            in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }
            or int(getattr(run, "resume_attempts", 0))
            > previous_resumes
        ):
            return
    except Exception:
        run = resolved.get_run(user_id, run_id)
        from .reconcile import classify_run_failure

        fallback = classify_run_failure(
            error=error,
            stopped=stopped,
            cancel_requested=bool(run.cancel_requested),
            checkpoint_exists=True,
            resume_attempts=int(getattr(run, "resume_attempts", 0)),
            max_resume_attempts=3,
        )
        if fallback == "resume":
            return
    for _attempt in range(2):
        try:
            if run.cancel_requested:
                resolved.request_cancel(
                    user_id,
                    run_id,
                    expected_version=run.version,
                )
                return
            resolved.transition_status(
                user_id,
                run_id,
                expected_version=run.version,
                new_status=RunStatus.FAILED,
                error_code=(
                    "rq_stopped"
                    if stopped
                    else type(error).__name__
                    if error
                    else "rq_failed"
                ),
                error_message=(
                    "RQ job stopped"
                    if stopped
                    else _safe_error_message(error)
                ),
            )
            return
        except VersionConflict:
            run = resolved.get_run(user_id, run_id)
            if run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return


def rq_failure_callback(
    job: Any,
    connection: Any,
    exc_type: Any = None,
    exc_value: BaseException | None = None,
    traceback: Any = None,
    *,
    repository: CommitteeRepository | None = None,
) -> None:
    del exc_type, traceback
    _terminalize_rq_job(
        job,
        connection=connection,
        repository=repository,
        stopped=False,
        error=exc_value,
    )


def rq_stopped_callback(
    job: Any,
    connection: Any,
    *args: Any,
    repository: CommitteeRepository | None = None,
) -> None:
    del args
    _terminalize_rq_job(
        job,
        connection=connection,
        repository=repository,
        stopped=True,
        error=None,
    )


def _publish(
    repository: CommitteeRepository,
    runtime: CommitteeRuntime,
    user_id: str,
    run_id: str,
    event_type: str,
    payload: Any,
    *,
    attempt: int,
    node: str,
    event_key: str,
) -> dict[str, Any]:
    durable = repository.append_outbox_event(
        user_id,
        run_id,
        attempt=attempt,
        node=node,
        event_type=event_type,
        event_key=event_key,
        payload=payload,
    )
    try:
        runtime.append_event(
            user_id,
            run_id,
            event_type,
            payload,
            event_id=f"{durable['sequence']}-0",
        )
        repository.mark_event_published(user_id, run_id, event_key)
    except Exception:
        pass
    return durable


def _republish_outbox(
    repository: CommitteeRepository,
    runtime: CommitteeRuntime,
    user_id: str,
    run_id: str,
) -> None:
    for event in repository.list_unpublished_events(user_id, run_id):
        try:
            runtime.append_event(
                user_id,
                run_id,
                str(event["event_type"]),
                event.get("payload") or {},
                event_id=f"{event['sequence']}-0",
            )
            repository.mark_event_published(
                user_id,
                run_id,
                str(event["event_key"]),
            )
        except Exception:
            return


def _persist_node_update(
    repository: CommitteeRepository,
    runtime: CommitteeRuntime,
    user_id: str,
    run_id: str,
    *,
    attempt: int,
    node: str,
    sequence: int,
    update: dict[str, Any],
) -> None:
    graph_events = update.get("events") or ()
    logical_key = (
        str(graph_events[-1].get("event_id"))
        if graph_events and isinstance(graph_events[-1], dict)
        else f"{node}:{sequence}"
    )
    logical_digest = hashlib.sha256(logical_key.encode()).hexdigest()[:24]
    for index, event in enumerate(graph_events):
        if not isinstance(event, dict):
            continue
        semantic_key = str(
            event.get("event_id")
            or f"{node}:{sequence}:event:{index}"
        )
        event_type = str(event.get("event_type") or "message")
        event_payload = event.get("payload") or {}
        _publish(
            repository,
            runtime,
            user_id,
            run_id,
            event_type,
            event_payload,
            attempt=attempt,
            node=str(event.get("node") or node),
            event_key=canonical_event_key(
                run_id,
                attempt,
                str(event.get("node") or node),
                event_type,
                event_payload,
                semantic_id=semantic_key,
            ),
        )
    for field, value in update.items():
        if field in {
            "events",
            "status",
            "started_at_epoch",
            "deadline_at_epoch",
        } or value is None:
            continue
        repository.upsert_artifact(
            user_id,
            run_id,
            kind=field,
            artifact_id=(
                f"attempt:{attempt}:node:{node}:event:{logical_digest}:"
                f"field:{field}"
            ),
            payload=value,
            attempt=attempt,
            node=node,
        )
    _publish(
        repository,
        runtime,
        user_id,
        run_id,
        "node_completed",
        {"node": node, "sequence": sequence, "fields": sorted(update)},
        attempt=attempt,
        node=node,
        event_key=canonical_event_key(
            run_id,
            attempt,
            node,
            "node_completed",
            {"node": node, "sequence": sequence, "fields": sorted(update)},
            semantic_id=f"{logical_key}:completed",
        ),
    )


def reconcile_checkpoint_to_mongo(
    repository: CommitteeRepository,
    runtime: CommitteeRuntime,
    user_id: str,
    run_id: str,
    state: dict[str, Any],
    *,
    attempt: int,
) -> None:
    """Idempotently rebuild durable audit records from checkpoint state."""
    artifact_fields = (
        "snapshot",
        "analyst_reports",
        "debate_turns",
        "trade_proposal",
        "trade_proposals",
        "backtest_verdict",
        "risk_verdict",
        "final_decision",
        "budget",
        "model_calls",
        "errors",
    )
    for field in artifact_fields:
        value = state.get(field)
        if value is None or value == []:
            continue
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(encoded.encode()).hexdigest()[:24]
        repository.upsert_artifact(
            user_id,
            run_id,
            kind=field,
            artifact_id=f"attempt:{attempt}:reconcile:{field}:{digest}",
            payload=value,
            attempt=attempt,
            node="checkpoint_reconcile",
        )
    for event in state.get("events") or ():
        if not isinstance(event, dict):
            continue
        semantic = str(
            event.get("event_id")
            or hashlib.sha256(
                json.dumps(event, sort_keys=True, default=str).encode()
            ).hexdigest()
        )
        event_node = str(event.get("node") or "unknown")
        event_type = str(event.get("event_type") or "message")
        event_payload = event.get("payload") or {}
        _publish(
            repository,
            runtime,
            user_id,
            run_id,
            event_type,
            event_payload,
            attempt=attempt,
            node=event_node,
            event_key=canonical_event_key(
                run_id,
                attempt,
                event_node,
                event_type,
                event_payload,
                semantic_id=semantic,
            ),
        )
    state_digest = hashlib.sha256(
        json.dumps(
            {
                "status": state.get("status"),
                "budget": state.get("budget"),
                "fields": [
                    field for field in artifact_fields if state.get(field) is not None
                ],
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()[:24]
    _publish(
        repository,
        runtime,
        user_id,
        run_id,
        "checkpoint_reconciled",
        {"status": state.get("status")},
        attempt=attempt,
        node="checkpoint_reconcile",
        event_key=f"attempt:{attempt}:reconcile:state:{state_digest}",
    )


async def _run_graph_stream(
    invoker: Any,
    payload: dict[str, Any] | None,
    *,
    repository: CommitteeRepository,
    runtime: CommitteeRuntime,
    user_id: str,
    run_id: str,
    attempt: int,
    job_id: str,
    renew_lease: Any | None = None,
) -> dict[str, Any]:
    config = {
        "configurable": {
            "thread_id": committee_thread_id(user_id, run_id),
        }
    }
    node_sequences: dict[str, int] = {}
    async for chunk in invoker.graph.astream(
        payload,
        config=config,
        stream_mode="updates",
    ):
        if renew_lease is not None:
            renew_lease()
        for node, update in dict(chunk).items():
            if isinstance(update, dict):
                node_name = str(node)
                node_sequences[node_name] = node_sequences.get(node_name, 0) + 1
                _persist_node_update(
                    repository,
                    runtime,
                    user_id,
                    run_id,
                    attempt=attempt,
                    node=node_name,
                    sequence=node_sequences[node_name],
                    update=update,
                )
        repository.touch_job_heartbeat(
            user_id,
            run_id,
            job_id=job_id,
        )
        checkpoint_state = await invoker.graph.aget_state(config)
        reconcile_checkpoint_to_mongo(
            repository,
            runtime,
            user_id,
            run_id,
            dict(checkpoint_state.values or {}),
            attempt=attempt,
        )
    state = await invoker.graph.aget_state(config)
    values = dict(state.values or {})
    reconcile_checkpoint_to_mongo(
        repository,
        runtime,
        user_id,
        run_id,
        values,
        attempt=attempt,
    )
    return values


async def _snapshot_loader(
    user_id: str,
    run_id: str,
    request: dict[str, Any],
):
    del run_id
    as_of = request.get("as_of")
    if isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    as_of = as_of or datetime.now(timezone.utc)
    requested = tuple(str(item) for item in request["universe"])
    symbols = [item for item in requested if not item.startswith("board:")]
    boards = [
        item.removeprefix("board:")
        for item in requested
        if item.startswith("board:")
    ]
    if boards:
        from ..universe import list_board_candidates

        for board in boards:
            candidates = await asyncio.to_thread(
                list_board_candidates,
                board,
                False,
            )
            symbols.extend(
                str(item["symbol"])
                for item in candidates
                if item.get("symbol")
            )
    universe = tuple(dict.fromkeys(symbols))
    if not universe:
        raise ValueError("committee universe resolved to empty")
    builder = SnapshotBuilder(default_collector_specs())
    return await asyncio.to_thread(
        builder.build,
        as_of=as_of,
        user_id=user_id,
        strategy_version=str(request["strategy_version"]),
        horizon="next_day",
        universe=universe,
    )


def execute_committee_job(user_id: str, run_id: str) -> dict[str, Any]:
    """Execute or resume a run; every exception is durably terminalized."""
    repository = CommitteeRepository.from_default_database()
    run = repository.get_run(user_id, run_id)
    if run.status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        return {"run_id": run_id, "status": run.status.value}
    runtime: CommitteeRuntime | None = None
    run_lock: Any | None = None
    lock_acquired = False
    execution_owner = uuid4().hex
    lease_claimed = False
    lease_stop = threading.Event()
    lease_thread: threading.Thread | None = None
    job_id: str | None = None
    checkpoint_exists = False
    try:
        settings = CommitteeRedisSettings.from_env()
        runtime = CommitteeRuntime(settings)
        run_lock = runtime.run_lock(user_id, run_id)
        lock_acquired = bool(run_lock.acquire())
        if not lock_acquired:
            raise VersionConflict("run Redis execution lease is active")
        run = repository.claim_execution_lease(
            user_id,
            run_id,
            owner=execution_owner,
            lease_seconds=settings.lock_ttl,
        )
        lease_claimed = True

        lease_errors: list[BaseException] = []

        def renew_once() -> None:
            if hasattr(run_lock, "extend"):
                run_lock.extend(
                    settings.lock_ttl,
                    replace_ttl=True,
                )
            if not repository.renew_execution_lease(
                user_id,
                run_id,
                owner=execution_owner,
                lease_seconds=settings.lock_ttl,
            ):
                raise VersionConflict("run Mongo execution lease was lost")
            if job_id is not None and not repository.touch_job_heartbeat(
                user_id,
                run_id,
                job_id=job_id,
            ):
                raise VersionConflict("run job heartbeat was lost")

        def renew_run_lease() -> None:
            if lease_errors:
                raise VersionConflict("run execution lease renewal failed")
            renew_once()

        def lease_heartbeat() -> None:
            interval = max(1.0, min(30.0, settings.lock_ttl / 3))
            while not lease_stop.wait(interval):
                try:
                    renew_once()
                except BaseException as exc:
                    lease_errors.append(exc)
                    return

        _republish_outbox(repository, runtime, user_id, run_id)
        checkpoint = initialize_checkpoint_saver(settings)
        if not checkpoint.ok or checkpoint.saver is None:
            raise RuntimeError(
                f"committee checkpoint unavailable: {checkpoint.status}"
            )
        config = dict(reload_config().get("committee") or {})
        total_timeout = float(
            (config.get("budget") or {}).get("total_timeout_seconds", 300)
        )
        if settings.job_timeout < total_timeout + 15:
            raise RuntimeError(
                "COMMITTEE_JOB_TIMEOUT must exceed graph total deadline by 15s"
            )
        if run.status is RunStatus.QUEUED:
            run = repository.transition_status(
                user_id,
                run_id,
                expected_version=run.version,
                new_status=RunStatus.RUNNING,
            )
        elif run.status is not RunStatus.RUNNING:
            raise IllegalStatusTransition("run is not queued or resumable")
        current_job = get_current_job()
        job_id = str(
            current_job.id
            if current_job is not None
            else run.queue_job_id or f"direct:{run_id}"
        )
        run = repository.record_job_started(
            user_id,
            run_id,
            job_id=job_id,
            deadline_at=datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp()
                + settings.job_timeout,
                tz=timezone.utc,
            ),
        )
        lease_thread = threading.Thread(
            target=lease_heartbeat,
            name=f"committee-lease-{run_id}",
            daemon=True,
        )
        lease_thread.start()
        if runtime.is_cancel_requested(user_id, run_id):
            run = repository.request_cancel(
                user_id,
                run_id,
                expected_version=run.version,
            )
            return {"run_id": run_id, "status": run.status.value}
        _publish(
            repository,
            runtime,
            user_id,
            run_id,
            "running",
            {},
            attempt=run.attempt,
            node="worker",
            event_key=f"attempt:{run.attempt}:running",
        )

        executor = _create_role_executor(
            config,
            runtime,
            user_id,
            run_id,
        )
        dependencies = replace(
            create_production_dependencies(executor, config),
            snapshot_loader=_snapshot_loader,
        )
        invoker = create_committee_invoker(
            dependencies=dependencies,
            checkpointer=checkpoint.saver,
            committee_config=config,
        )
        graph_config = {
            "configurable": {
                "thread_id": committee_thread_id(user_id, run_id),
            }
        }
        checkpoint_state = asyncio.run(
            invoker.graph.aget_state(graph_config)
        )
        checkpoint_exists = bool(checkpoint_state.values)
        if checkpoint_exists:
            reconcile_checkpoint_to_mongo(
                repository,
                runtime,
                user_id,
                run_id,
                dict(checkpoint_state.values or {}),
                attempt=run.attempt,
            )
        payload = _recovery_payload(
            user_id,
            run_id,
            run.initial_input,
            checkpoint_exists=checkpoint_exists,
            attempt=run.attempt,
        )
        if not checkpoint_exists:
            assert payload is not None
            payload.setdefault(
                "limits",
                invoker.default_limits.model_dump(mode="json"),
            )
            payload.setdefault(
                "max_debate_rounds",
                invoker.default_debate_rounds,
            )
        renew_run_lease()
        result = asyncio.run(
            _run_graph_stream(
                invoker,
                payload,
                repository=repository,
                runtime=runtime,
                user_id=user_id,
                run_id=run_id,
                attempt=run.attempt,
                job_id=job_id,
                renew_lease=renew_run_lease,
            )
        )
        run = repository.get_run(user_id, run_id)
        if runtime.is_cancel_requested(user_id, run_id):
            terminal = repository.request_cancel(
                user_id,
                run_id,
                expected_version=run.version,
            )
        elif result.get("status") == "completed":
            snapshot_id = (result.get("snapshot") or {}).get("snapshot_id")
            terminal = repository.transition_status(
                user_id,
                run_id,
                expected_version=run.version,
                new_status=RunStatus.COMPLETED,
                snapshot_id=snapshot_id,
            )
        else:
            terminal = repository.transition_status(
                user_id,
                run_id,
                expected_version=run.version,
                new_status=RunStatus.FAILED,
                error_code="graph_aborted",
                error_message="委员会图未完成",
            )
        _publish(
            repository,
            runtime,
            user_id,
            run_id,
            terminal.status.value,
            {"version": terminal.version},
            attempt=run.attempt,
            node="worker",
            event_key=f"attempt:{run.attempt}:terminal:{terminal.status.value}",
        )
        reconcile_checkpoint_to_mongo(
            repository,
            runtime,
            user_id,
            run_id,
            result,
            attempt=run.attempt,
        )
        return {"run_id": run_id, "status": terminal.status.value}
    except BaseException as exc:
        if not lease_claimed:
            raise
        try:
            current = repository.get_run(user_id, run_id)
            if current.status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                from .reconcile import classify_run_failure

                disposition = classify_run_failure(
                    error=exc,
                    stopped=False,
                    cancel_requested=current.cancel_requested,
                    checkpoint_exists=checkpoint_exists,
                    resume_attempts=current.resume_attempts,
                    max_resume_attempts=3,
                )
                if disposition == "cancelled":
                    repository.request_cancel(
                        user_id,
                        run_id,
                        expected_version=current.version,
                    )
                elif disposition == "failed":
                    current = repository.transition_status(
                        user_id,
                        run_id,
                        expected_version=current.version,
                        new_status=RunStatus.FAILED,
                        error_code=type(exc).__name__[:128],
                        error_message=_safe_error_message(exc),
                    )
                elif runtime is not None:
                    _publish(
                        repository,
                        runtime,
                        user_id,
                        run_id,
                        "execution_interrupted",
                        {
                            "error_code": type(exc).__name__[:128],
                            "resumable": True,
                        },
                        attempt=current.attempt,
                        node="worker",
                        event_key=canonical_event_key(
                            run_id,
                            current.attempt,
                            "worker",
                            "execution_interrupted",
                            {
                                "error_code": type(exc).__name__[:128],
                                "resumable": True,
                            },
                            semantic_id=(
                                f"resume:{current.resume_attempts}:"
                                f"{type(exc).__name__}"
                            ),
                        ),
                    )
        except Exception:
            pass
        raise
    finally:
        lease_stop.set()
        if lease_thread is not None:
            lease_thread.join(timeout=2)
        if lease_claimed:
            try:
                repository.release_execution_lease(
                    user_id,
                    run_id,
                    owner=execution_owner,
                )
            except Exception:
                pass
        if lock_acquired and run_lock is not None:
            try:
                run_lock.release()
            except Exception:
                pass
