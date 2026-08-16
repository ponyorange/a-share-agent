from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import get_current_user
from tests.committee_http_app import leftover_committee_app
from app.advisor.committee import routes as committee_routes
from app.advisor.agent.tools import build_tools
from app.advisor.committee.approval import (
    ApprovalPlan,
    PlannedOrder,
    approval_plan_hash,
    plans_match,
)
from app.advisor.committee.jobs import enqueue_committee_run
from app.advisor.committee.models import RunStatus
from app.advisor.committee.chat_stream import ChatStreamEvent
from app.advisor.committee.routes import (
    _event_stream,
    parse_last_event_id,
)
from app.advisor.committee.reconcile import (
    classify_run_failure,
    reconcile_stale_runs,
)
from app.advisor.committee.redis_client import CommitteeRedisSettings
from app.advisor.committee.tasks import (
    _create_role_executor,
    _recovery_payload,
    rq_failure_callback,
    rq_stopped_callback,
)
from app.advisor.committee.runtime import CommitteeRuntime, RuntimeEvent


NOW = datetime(2026, 7, 22, 2, tzinfo=timezone.utc)


def _plan(price: float = 10) -> ApprovalPlan:
    return ApprovalPlan(
        proposal_hash="a" * 64,
        decision_hash="b" * 64,
        account_version=3,
        orders=(
            PlannedOrder(
                symbol="510300",
                side="buy",
                qty=100,
                price=price,
            ),
        ),
    )


def test_http_committee_routes_require_authentication():
    client = TestClient(leftover_committee_app())
    for method, path, kwargs in (
        ("get", "/api/advisor/committee/runs", {}),
        (
            "post",
            "/api/advisor/committee/runs",
            {
                "headers": {"Idempotency-Key": "x"},
                "json": {
                    "symbols": ["510300"],
                    "horizon": "next_day",
                    "strategy_version": "v1",
                },
            },
        ),
        ("get", "/api/advisor/committee/runs/r/events", {}),
        ("delete", "/api/advisor/committee/runs/r", {}),
        ("post", "/api/advisor/committee/runs/r/cancel", {}),
        ("post", "/api/advisor/committee/runs/r/retry", {"headers": {"Idempotency-Key": "x"}}),
        ("get", "/api/advisor/committee/runs/r/order-preview", {}),
        (
            "post",
            "/api/advisor/committee/runs/r/approve",
            {
                "headers": {"Idempotency-Key": "x"},
                "json": {
                    "preview_id": "p",
                    "decision_hash": "b" * 64,
                    "proposal_hash": "a" * 64,
                    "account_version": 1,
                    "confirm": True,
                },
            },
        ),
    ):
        assert getattr(client, method)(path, **kwargs).status_code == 401


def test_http_run_list_uses_authenticated_user_scope(monkeypatch):
    seen = []

    class Repository:
        def list_runs(self, user_id, *, limit):
            seen.append((user_id, limit))
            return []

    monkeypatch.setattr(committee_routes, "_repository", lambda: Repository())
    test_app = leftover_committee_app()
    test_app.dependency_overrides[get_current_user] = lambda: {
        "id": "alice",
        "username": "alice",
    }
    try:
        response = TestClient(test_app).get(
            "/api/advisor/committee/runs?limit=7"
        )
    finally:
        test_app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"runs": []}
    assert seen == [("alice", 7)]


def test_http_delete_run_is_user_scoped_and_maps_domain_errors(monkeypatch):
    repository = Mock()
    repository.soft_delete_run.return_value = SimpleNamespace()
    monkeypatch.setattr(committee_routes, "_plain_repository", lambda: repository)
    test_app = leftover_committee_app()
    test_app.dependency_overrides[get_current_user] = lambda: {
        "id": "alice",
        "username": "alice",
    }
    try:
        response = TestClient(test_app).delete(
            "/api/advisor/committee/runs/run-1"
        )
    finally:
        test_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "deleted": True}
    args = repository.soft_delete_run.call_args
    assert args.args[:2] == ("alice", "run-1")
    assert args.kwargs["deleted_by"] == "alice"
    assert args.kwargs["deleted_at"].tzinfo is not None


def test_http_delete_run_does_not_touch_task_infrastructure(monkeypatch):
    repository = Mock()
    repository.soft_delete_run.return_value = SimpleNamespace()
    monkeypatch.setattr(
        committee_routes.CommitteeRepository,
        "from_default_database",
        Mock(return_value=repository),
    )

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("DELETE must not touch task infrastructure")

    for name in (
        "create_queue",
        "reconcile_stale_runs",
        "initialize_checkpoint_saver",
        "_infra",
    ):
        monkeypatch.setattr(committee_routes, name, fail_if_called)

    test_app = leftover_committee_app()
    test_app.dependency_overrides[get_current_user] = lambda: {
        "id": "alice",
        "username": "alice",
    }
    try:
        response = TestClient(test_app).delete(
            "/api/advisor/committee/runs/run-1"
        )
    finally:
        test_app.dependency_overrides.clear()

    assert response.status_code == 200
    repository.soft_delete_run.assert_called_once()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (committee_routes.RunNotFound("missing"), 404),
        (committee_routes.IllegalStatusTransition("active"), 409),
        (committee_routes.VersionConflict("changed"), 409),
    ],
)
def test_http_delete_run_maps_errors(monkeypatch, error, status):
    repository = Mock()
    repository.soft_delete_run.side_effect = error
    monkeypatch.setattr(committee_routes, "_plain_repository", lambda: repository)
    test_app = leftover_committee_app()
    test_app.dependency_overrides[get_current_user] = lambda: {
        "id": "alice",
        "username": "alice",
    }
    try:
        response = TestClient(test_app).delete(
            "/api/advisor/committee/runs/run-1"
        )
    finally:
        test_app.dependency_overrides.clear()
    assert response.status_code == status


def test_last_event_id_accepts_durable_and_live_frame_ids():
    assert parse_last_event_id(None) == 0
    assert parse_last_event_id("42") == 42
    assert parse_last_event_id("42-0") == 42
    assert parse_last_event_id("42-live-7") == 42
    assert parse_last_event_id("2-terminal") == 2
    for value in ("-1", " 1", "1 ", "abc", "01", "1-live-x"):
        with pytest.raises(HTTPException) as exc:
            parse_last_event_id(value)
        assert exc.value.status_code == 400


def test_approval_plan_hash_comparison_detects_any_stale_change():
    original = _plan()
    assert plans_match(original, original)
    assert not plans_match(original, _plan(price=10.01))
    assert approval_plan_hash(original) != approval_plan_hash(_plan(price=10.01))


def test_checkpoint_recovery_uses_initial_input_only_without_checkpoint():
    initial = {"snapshot_request": {"universe": ["510300"]}, "attempt": 2}
    assert _recovery_payload(
        "u", "r", initial, checkpoint_exists=False, attempt=3
    ) == {
        "user_id": "u",
        "run_id": "r",
        "snapshot_request": {"universe": ["510300"]},
        "attempt": 3,
    }
    assert (
        _recovery_payload(
            "u", "r", initial, checkpoint_exists=True, attempt=3
        )
        is None
    )


def test_role_executor_injects_isolated_ephemeral_stream_sink(monkeypatch):
    captured = {}

    class Runner:
        def __init__(self, config, *, stream_sink):
            captured["config"] = config
            captured["sink"] = stream_sink

    monkeypatch.setattr(
        "app.advisor.committee.tasks.ChatModelRoleRunner", Runner
    )
    monkeypatch.setattr(
        "app.advisor.committee.tasks.RoleAgentExecutor", lambda runner: runner
    )
    runtime = Mock()
    runtime.append_ephemeral_event.side_effect = [
        RuntimeError("redis down"),
        None,
    ]

    executor = _create_role_executor({"models": {}}, runtime, "u", "r")
    event = ChatStreamEvent(
        event_type="message_delta",
        payload={"message_id": "m1", "delta": "A"},
    )
    asyncio.run(captured["sink"](event))
    asyncio.run(captured["sink"](event))

    assert executor is not None
    assert captured["config"] == {"models": {}}
    assert runtime.append_ephemeral_event.call_args_list == [
        call("u", "r", "message_delta", event.payload),
        call("u", "r", "message_delta", event.payload),
    ]


def test_enqueue_registers_failure_and_stopped_callbacks():
    queue = Mock()
    queue.enqueue_call.return_value = "job"
    settings = SimpleNamespace(
        enabled=True,
        job_timeout=30,
        result_ttl=60,
        failure_ttl=120,
    )
    job = enqueue_committee_run(
        "u",
        "r",
        "key",
        settings=settings,
        queue=queue,
    )
    assert job == "job"
    call = queue.enqueue_call.call_args.kwargs
    assert call["on_failure"] is rq_failure_callback
    assert call["on_stopped"] is rq_stopped_callback
    assert call["meta"]["user_id"] == "u"
    assert call["meta"]["run_id"] == "r"


class _CallbackRepository:
    def __init__(self, status=RunStatus.RUNNING, cancel_requested=False):
        self.run = SimpleNamespace(
            user_id="u",
            run_id="r",
            status=status,
            version=4,
            cancel_requested=cancel_requested,
        )
        self.transitions = []
        self.last_error_message = None

    def get_run(self, user_id, run_id):
        assert (user_id, run_id) == ("u", "r")
        return self.run

    def transition_status(self, user_id, run_id, **kwargs):
        self.transitions.append(kwargs["new_status"])
        self.last_error_message = kwargs.get("error_message")
        self.run.status = kwargs["new_status"]
        return self.run

    def request_cancel(self, user_id, run_id, **kwargs):
        self.transitions.append(RunStatus.CANCELLED)
        self.run.status = RunStatus.CANCELLED
        return self.run


def test_rq_failure_callback_prefers_cancel_over_failed():
    repository = _CallbackRepository(cancel_requested=True)
    job = SimpleNamespace(meta={"user_id": "u", "run_id": "r"}, id="job")
    rq_failure_callback(
        job,
        object(),
        RuntimeError,
        RuntimeError("timeout"),
        None,
        repository=repository,
    )
    assert repository.transitions == [RunStatus.CANCELLED]


def test_rq_failure_callback_redacts_configured_redis_password(monkeypatch):
    repository = _CallbackRepository()
    monkeypatch.setenv("REDIS_PASSWORD", "callback-secret")
    job = SimpleNamespace(meta={"user_id": "u", "run_id": "r"}, id="job")

    rq_failure_callback(
        job,
        object(),
        ValueError,
        ValueError("invalid config callback-secret"),
        None,
        repository=repository,
    )

    assert repository.transitions == [RunStatus.FAILED]
    assert "callback-secret" not in repository.last_error_message


def test_rq_stopped_callback_only_cancels_explicit_user_request():
    repository = _CallbackRepository()
    job = SimpleNamespace(meta={"user_id": "u", "run_id": "r"}, id="job")
    rq_stopped_callback(
        job,
        object(),
        repository=repository,
    )
    assert repository.transitions == []

    cancelled = _CallbackRepository(cancel_requested=True)
    rq_stopped_callback(
        job,
        object(),
        repository=cancelled,
    )
    assert cancelled.transitions == [RunStatus.CANCELLED]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("timeout"), "resume"),
        (RuntimeError("worker lost"), "resume"),
        (ConnectionError("redis unavailable"), "resume"),
        (ValueError("invalid deterministic config"), "failed"),
    ],
)
def test_failure_classification_resumes_transient_only(error, expected):
    assert classify_run_failure(
        error=error,
        stopped=False,
        cancel_requested=False,
        checkpoint_exists=True,
        resume_attempts=0,
        max_resume_attempts=3,
    ) == expected
    assert classify_run_failure(
        error=error,
        stopped=False,
        cancel_requested=True,
        checkpoint_exists=True,
        resume_attempts=0,
        max_resume_attempts=3,
    ) == "cancelled"
    assert classify_run_failure(
        error=error,
        stopped=True,
        cancel_requested=False,
        checkpoint_exists=True,
        resume_attempts=3,
        max_resume_attempts=3,
    ) == "failed"
    assert classify_run_failure(
        error=None,
        stopped=True,
        cancel_requested=False,
        checkpoint_exists=True,
        resume_attempts=0,
        max_resume_attempts=3,
    ) == "resume"


def test_watchdog_marks_missing_stale_job_failed(monkeypatch):
    repository = _CallbackRepository(status=RunStatus.RUNNING)
    repository.run.user_id = "u"
    repository.run.updated_at = NOW - timedelta(seconds=500)
    repository.run.job_heartbeat_at = NOW - timedelta(seconds=500)
    repository.run.job_deadline_at = NOW - timedelta(seconds=1)
    repository.run.queue_job_id = "missing"
    repository.list_stale_runs = lambda **_kwargs: [repository.run]
    monkeypatch.setattr(
        "app.advisor.committee.reconcile.Job.fetch",
        Mock(side_effect=RuntimeError("missing")),
    )
    recovered = reconcile_stale_runs(
        repository=repository,
        connection=object(),
        now=NOW,
    )
    assert recovered == ["r"]
    assert repository.transitions == [RunStatus.FAILED]


def test_watchdog_reenqueues_same_run_when_checkpoint_exists(monkeypatch):
    repository = _CallbackRepository(status=RunStatus.RUNNING)
    repository.run.user_id = "u"
    repository.run.run_id = "r"
    repository.run.updated_at = NOW - timedelta(seconds=500)
    repository.run.job_heartbeat_at = NOW - timedelta(seconds=500)
    repository.run.job_deadline_at = NOW - timedelta(seconds=1)
    repository.run.queue_job_id = "missing"
    repository.run.resume_attempts = 0
    repository.list_stale_runs = lambda **_kwargs: [repository.run]
    repository.record_resume_enqueued = Mock(
        side_effect=lambda *args, **kwargs: repository.run
    )
    repository.append_outbox_event = Mock(return_value={"event_id": "1"})
    monkeypatch.setattr(
        "app.advisor.committee.reconcile.Job.fetch",
        Mock(side_effect=RuntimeError("missing")),
    )
    enqueued = Mock()

    recovered = reconcile_stale_runs(
        repository=repository,
        connection=object(),
        now=NOW,
        checkpoint_exists=lambda user_id, run_id: True,
        enqueue_resume=enqueued,
    )

    assert recovered == ["r"]
    enqueued.assert_called_once_with("u", "r", 1)
    repository.record_resume_enqueued.assert_called_once()
    assert repository.transitions == []


def test_watchdog_default_resume_uses_delayed_scheduler(monkeypatch):
    repository = _CallbackRepository(status=RunStatus.RUNNING)
    repository.run.user_id = "u"
    repository.run.run_id = "r"
    repository.run.updated_at = NOW - timedelta(seconds=500)
    repository.run.job_heartbeat_at = NOW - timedelta(seconds=500)
    repository.run.job_deadline_at = NOW - timedelta(seconds=1)
    repository.run.queue_job_id = "missing"
    repository.run.resume_attempts = 0
    repository.list_stale_runs = lambda **_kwargs: [repository.run]
    repository.record_resume_enqueued = Mock(
        side_effect=lambda *args, **kwargs: repository.run
    )
    repository.append_outbox_event = Mock(return_value={"event_id": "1"})
    monkeypatch.setattr(
        "app.advisor.committee.reconcile.Job.fetch",
        Mock(side_effect=RuntimeError("missing")),
    )
    settings = SimpleNamespace(
        job_timeout=30,
        result_ttl=60,
        failure_ttl=120,
        key=lambda *parts: ":".join(parts),
    )
    monkeypatch.setattr(
        "app.advisor.committee.redis_client.CommitteeRedisSettings.from_env",
        lambda: settings,
    )
    queue = Mock()
    queue.enqueue_in.return_value = SimpleNamespace(id="resume-job")
    monkeypatch.setattr(
        "app.advisor.committee.jobs.create_queue",
        lambda *_args, **_kwargs: queue,
    )

    reconcile_stale_runs(
        repository=repository,
        connection=object(),
        now=NOW,
        checkpoint_exists=lambda user_id, run_id: True,
    )

    delay, function, user_id, run_id = queue.enqueue_in.call_args.args
    assert delay == timedelta(seconds=5)
    assert function == "app.advisor.committee.tasks.execute_committee_job"
    assert (user_id, run_id) == ("u", "r")
    assert queue.enqueue_in.call_args.kwargs["on_failure"] is rq_failure_callback
    assert queue.enqueue_in.call_args.kwargs["on_stopped"] is rq_stopped_callback


def test_sse_drains_mongo_before_touching_redis():
    class Request:
        async def is_disconnected(self):
            return False

    class Repository:
        def __init__(self):
            self.calls = 0

        def list_events_after(self, user_id, run_id, *, after_sequence, limit):
            self.calls += 1
            if self.calls == 1:
                return [
                    {
                        "sequence": 1,
                        "event_id": "1",
                        "event_type": "queued",
                        "payload": {},
                    }
                ]
            return []

        def get_run(self, user_id, run_id):
            return SimpleNamespace(status=RunStatus.COMPLETED)

    runtime = Mock()

    async def collect():
        return [
            item
            async for item in _event_stream(
                Request(), Repository(), runtime, "u", "r", 0
            )
        ]

    rows = asyncio.run(collect())
    assert "id: 1" in rows[0]
    assert any("event: completed" in item for item in rows)
    runtime.read_events_after.assert_not_called()


def test_sse_interleaves_durable_mongo_and_ephemeral_redis_without_cursor_leak():
    class Request:
        async def is_disconnected(self):
            return False

    class Repository:
        def __init__(self):
            self.after_one_calls = 0

        def list_events_after(self, user_id, run_id, *, after_sequence, limit):
            if after_sequence == 0:
                return [
                    {
                        "sequence": 1,
                        "event_id": "1",
                        "event_type": "running",
                        "payload": {},
                    }
                ]
            if after_sequence == 1:
                self.after_one_calls += 1
                if self.after_one_calls == 2:
                    return [
                        {
                            "sequence": 2,
                            "event_id": "2",
                            "event_type": "message_completed",
                            "payload": {
                                "message_id": "m1",
                                "content": "完整结论",
                            },
                        }
                    ]
            return []

        def get_run(self, user_id, run_id):
            status = (
                RunStatus.COMPLETED
                if self.after_one_calls >= 2
                else RunStatus.RUNNING
            )
            return SimpleNamespace(status=status)

    runtime = Mock()
    runtime.read_ephemeral_events_after.side_effect = [
        [
            RuntimeEvent(
                event_id="99-0",
                event_type="node_completed",
                payload={"node": "technical"},
            ),
            RuntimeEvent(
                event_id="100-0",
                event_type="message_delta",
                payload={"message_id": "m1", "delta": "完整"},
            )
        ]
    ]

    async def collect():
        return [
            item
            async for item in _event_stream(
                Request(), Repository(), runtime, "u", "r", 0
            )
        ]

    frames = asyncio.run(collect())
    assert [next(line for line in frame.splitlines() if line.startswith("event:")) for frame in frames] == [
        "event: running",
        "event: message_delta",
        "event: message_completed",
        "event: completed",
    ]
    assert "id: 1-live-1" in frames[1]
    runtime.read_ephemeral_events_after.assert_called_once_with(
        "u",
        "r",
        last_event_id="$",
        count=20,
        block_ms=1000,
    )


def test_sse_reconnect_drops_live_delta_but_keeps_next_completed_message():
    class Request:
        async def is_disconnected(self):
            return False

    class Repository:
        def __init__(self):
            self.calls = 0

        def list_events_after(self, user_id, run_id, *, after_sequence, limit):
            assert after_sequence in {1, 2}
            self.calls += 1
            if self.calls == 2:
                return [
                    {
                        "sequence": 2,
                        "event_id": "2",
                        "event_type": "message_completed",
                        "payload": {
                            "message_id": "m1",
                            "content": "完整结论",
                        },
                    }
                ]
            return []

        def get_run(self, user_id, run_id):
            status = (
                RunStatus.COMPLETED
                if self.calls >= 2
                else RunStatus.RUNNING
            )
            return SimpleNamespace(status=status)

    class ReconnectRedis:
        def __init__(self):
            self.streams = {}
            self.on_xread = None

        def xadd(self, key, fields, **kwargs):
            del kwargs
            stream = self.streams.setdefault(key, [])
            event_id = f"{len(stream) + 1}-0"
            stream.append((event_id, dict(fields)))
            return event_id

        def expire(self, key, ttl):
            del key, ttl
            return True

        def xread(self, streams, count=None, block=None):
            del block
            key, after = next(iter(streams.items()))
            assert after == "$"
            existing = self.streams.get(key, [])
            boundary = existing[-1][0] if existing else "0-0"
            callback, self.on_xread = self.on_xread, None
            if callback is not None:
                callback()
            boundary_number = int(boundary.split("-", 1)[0])
            rows = [
                row
                for row in self.streams.get(key, [])
                if int(row[0].split("-", 1)[0]) > boundary_number
            ][:count]
            return [(key, rows)] if rows else []

    redis = ReconnectRedis()
    settings = CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "redis.invalid",
        }
    )
    runtime = CommitteeRuntime(settings, client=redis)
    runtime.append_ephemeral_event(
        "u",
        "r",
        "message_delta",
        {"message_id": "m1", "delta": "旧片段"},
    )
    redis.on_xread = lambda: runtime.append_ephemeral_event(
        "u",
        "r",
        "message_delta",
        {"message_id": "m1", "delta": "新片段"},
    )

    async def collect():
        return [
            item
            async for item in _event_stream(
                Request(),
                Repository(),
                runtime,
                "u",
                "r",
                parse_last_event_id("1-live-9"),
            )
        ]

    frames = asyncio.run(collect())
    assert "新片段" in frames[0]
    assert all("旧片段" not in frame for frame in frames)
    assert "event: message_completed" in frames[1]


def test_sse_flushes_completed_message_before_synthetic_terminal_frame():
    class Request:
        async def is_disconnected(self):
            return False

    repository = Mock()
    repository.list_events_after.side_effect = [
        [
            {
                "sequence": 1,
                "event_id": "1",
                "event_type": "message_completed",
                "payload": {"message_id": "m1", "content": "最终消息"},
            }
        ],
        [],
    ]
    repository.get_run.return_value = SimpleNamespace(
        status=RunStatus.COMPLETED
    )
    runtime = Mock()

    async def collect():
        return [
            item
            async for item in _event_stream(
                Request(), repository, runtime, "u", "r", 0
            )
        ]

    frames = asyncio.run(collect())
    assert "event: message_completed" in frames[0]
    assert "event: completed" in frames[1]
    runtime.read_ephemeral_events_after.assert_not_called()


def test_sse_emits_terminal_event_when_run_already_failed():
    class Request:
        async def is_disconnected(self):
            return False

    repository = Mock()
    repository.list_events_after.return_value = []
    repository.get_run.return_value = SimpleNamespace(
        status=RunStatus.FAILED,
        error_code="ValidationError",
        error_message="extra inputs are not permitted",
    )
    runtime = Mock()

    async def collect():
        return [
            item
            async for item in _event_stream(
                Request(), repository, runtime, "u", "r", 0
            )
        ]

    rows = asyncio.run(collect())
    assert len(rows) == 1
    assert "event: failed" in rows[0]
    assert "ValidationError" in rows[0]
    runtime.read_events_after.assert_not_called()


def test_sse_stops_immediately_when_client_disconnects():
    request = SimpleNamespace(
        is_disconnected=Mock(return_value=asyncio.sleep(0, result=True))
    )
    repository = Mock()
    runtime = Mock()

    async def collect():
        return [
            item
            async for item in _event_stream(
                request, repository, runtime, "u", "r", 0
            )
        ]

    assert asyncio.run(collect()) == []
    repository.list_events_after.assert_not_called()
    runtime.read_events_after.assert_not_called()


def test_sse_emits_heartbeat_when_mongo_and_redis_are_idle():
    class Request:
        async def is_disconnected(self):
            return False

    repository = Mock()
    repository.list_events_after.return_value = []
    repository.get_run.return_value = SimpleNamespace(status=RunStatus.RUNNING)
    runtime = Mock()
    runtime.read_ephemeral_events_after.return_value = []

    async def first():
        stream = _event_stream(Request(), repository, runtime, "u", "r", 0)
        try:
            return await anext(stream)
        finally:
            await stream.aclose()

    assert asyncio.run(first()) == ": heartbeat\n\n"


def test_agent_exposes_no_committee_tools(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.tools.load_portfolio",
        lambda _uid: {"positions": []},
    )
    tools = build_tools("u")
    assert {item.name for item in tools if "committee" in item.name} == set()


def test_no_committee_run_write_tool_accepts_run_id():
    for tool in build_tools("u"):
        assert "committee" not in tool.name
        signature = inspect.signature(tool.func)
        assert not (
            "run_id" in signature.parameters
            and any(
                word in tool.name
                for word in ("apply", "place", "sell", "reset", "delete")
            )
        )
