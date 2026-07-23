from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

import pytest
from redis.exceptions import LockNotOwnedError
from redis.lock import Lock
import threading

from app.advisor.committee.redis_client import (
    CommitteeDisabledError,
    CommitteeRedisSettings,
)
from app.advisor.committee.runtime import (
    CommitteeRuntime,
    RuntimeLockError,
)


class FakeRedis:
    def __init__(self):
        self.streams = {}
        self.values = {}
        self.expiries = {}
        self.locks = []
        self.on_xread = None
        self.xadd_ids = []

    def xadd(self, key, fields, maxlen=None, approximate=None, id=None):
        self.xadd_ids.append((key, id))
        stream = self.streams.setdefault(key, [])
        event_id = id or f"{len(stream) + 1}-0"
        if stream:
            previous = tuple(int(part) for part in stream[-1][0].split("-"))
            current = tuple(int(part) for part in event_id.split("-"))
            if current <= previous:
                raise ValueError("Redis stream IDs must be monotonic")
        stream.append((event_id, dict(fields)))
        return event_id

    def xread(self, streams, count=None, block=None):
        key, after = next(iter(streams.items()))
        if after == "$":
            existing = self.streams.get(key, [])
            after = existing[-1][0] if existing else "0-0"
            if self.on_xread is not None:
                callback, self.on_xread = self.on_xread, None
                callback()
        after_number = int(str(after).split("-", 1)[0])
        rows = [
            row
            for row in self.streams.get(key, [])
            if int(row[0].split("-", 1)[0]) > after_number
        ]
        if count is not None:
            rows = rows[:count]
        return [(key, rows)] if rows else []

    def lock(self, name, timeout, blocking_timeout, thread_local=False):
        result = (name, timeout, blocking_timeout)
        self.locks.append(result)
        return result

    def set(self, key, value, ex):
        self.values[key] = value
        self.expiries[key] = ex
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        self.expiries.pop(key, None)
        return int(existed)

    def expire(self, key, ttl):
        if key not in self.streams:
            return False
        self.expiries[key] = ttl
        return True


def _settings(enabled=True):
    values = {
        "COMMITTEE_ENABLED": "true" if enabled else "false",
        "COMMITTEE_KEY_PREFIX": "tenant:committee",
        "COMMITTEE_LOCK_TTL": "77",
        "COMMITTEE_RESULT_TTL": "600",
    }
    if enabled:
        values["REDIS_HOST"] = "redis.invalid"
    return CommitteeRedisSettings.from_env(values)


def test_stream_read_after_supports_last_event_id():
    fake = FakeRedis()
    runtime = CommitteeRuntime(_settings(), client=fake)
    first = runtime.append_event("alice", "run-1", "progress", {"step": 1})
    second = runtime.append_event("alice", "run-1", "progress", {"step": 2})

    events = runtime.read_events_after(
        "alice", "run-1", last_event_id=first.event_id
    )

    assert [event.event_id for event in events] == [second.event_id]
    assert events[0].payload == {"step": 2}


def test_ephemeral_stream_is_independent_from_durable_sequence_ids():
    fake = FakeRedis()
    runtime = CommitteeRuntime(_settings(), client=fake)

    runtime.append_event(
        "alice", "run-1", "running", {}, event_id="1-0"
    )
    first = runtime.append_ephemeral_event(
        "alice",
        "run-1",
        "message_delta",
        {"message_id": "m1", "delta": "A", "offset": 0, "generation": 1},
    )
    second = runtime.append_ephemeral_event(
        "alice",
        "run-1",
        "message_delta",
        {"message_id": "m1", "delta": "B", "offset": 1, "generation": 1},
    )
    runtime.append_event(
        "alice", "run-1", "message_completed", {}, event_id="2-0"
    )

    assert first.event_id != second.event_id
    assert len(fake.streams) == 2
    ephemeral_key = runtime._ephemeral_stream_key("alice", "run-1")
    assert [
        event_id for key, event_id in fake.xadd_ids if key == ephemeral_key
    ] == [None, None]
    assert [
        event.event_id
        for event in runtime.read_ephemeral_events_after(
            "alice", "run-1", last_event_id=first.event_id
        )
    ] == [second.event_id]
    durable_key = runtime._stream_key("alice", "run-1")
    assert [event_id for event_id, _fields in fake.streams[durable_key]] == [
        "1-0",
        "2-0",
    ]


def test_ephemeral_stream_gets_short_lifecycle_ttl():
    fake = FakeRedis()
    runtime = CommitteeRuntime(_settings(), client=fake)

    runtime.append_ephemeral_event(
        "alice",
        "run-1",
        "message_delta",
        {"message_id": "m1", "delta": "A"},
    )

    ephemeral_key = runtime._ephemeral_stream_key("alice", "run-1")
    assert fake.expiries[ephemeral_key] == runtime.settings.result_ttl


def test_ephemeral_dollar_cursor_drops_existing_and_reads_new_messages():
    fake = FakeRedis()
    runtime = CommitteeRuntime(_settings(), client=fake)
    runtime.append_ephemeral_event(
        "alice",
        "run-1",
        "message_delta",
        {"message_id": "m1", "delta": "old"},
    )
    fake.on_xread = lambda: runtime.append_ephemeral_event(
        "alice",
        "run-1",
        "message_delta",
        {"message_id": "m1", "delta": "new"},
    )

    events = runtime.read_ephemeral_events_after("alice", "run-1")

    assert [event.payload["delta"] for event in events] == ["new"]


def test_runtime_lock_and_cache_use_prefix_and_ttl():
    fake = FakeRedis()
    runtime = CommitteeRuntime(_settings(), client=fake)

    assert runtime.run_lock("alice", "run-1") == (
        "tenant:committee:lock:run:alice:run-1",
        77,
        0,
    )
    assert runtime.cache_set("snapshot", "abc", {"value": 3}, ttl=15)
    assert fake.expiries["tenant:committee:cache:snapshot:abc"] == 15
    assert runtime.cache_get("snapshot", "abc") == {"value": 3}
    assert runtime.cache_delete("snapshot", "abc") is True


def test_disabled_runtime_is_explicit_and_has_no_process_fallback():
    fake = FakeRedis()
    runtime = CommitteeRuntime(_settings(enabled=False), client=fake)

    with pytest.raises(CommitteeDisabledError):
        runtime.append_event("alice", "run-1", "progress", {})
    with pytest.raises(CommitteeDisabledError):
        runtime.cache_set("snapshot", "abc", {"value": 3})
    assert fake.streams == {}
    assert fake.values == {}


def test_stream_decodes_realistic_redis_bytes():
    class BytesRedis(FakeRedis):
        def xadd(self, key, fields, maxlen=None, approximate=None):
            event_id = super().xadd(key, fields, maxlen, approximate)
            return event_id.encode()

        def xread(self, streams, count=None, block=None):
            rows = super().xread(streams, count, block)
            return [
                (
                    key.encode(),
                    [
                        (
                            event_id.encode(),
                            {
                                str(field).encode(): str(value).encode()
                                for field, value in fields.items()
                            },
                        )
                        for event_id, fields in messages
                    ],
                )
                for key, messages in rows
            ]

    runtime = CommitteeRuntime(_settings(), client=BytesRedis())
    first = runtime.append_event("alice", "run-1", "progress", {"step": 1})

    events = runtime.read_events_after(
        "alice", "run-1", last_event_id="0-0"
    )
    assert first.event_id == "1-0"
    assert events[0].event_id == "1-0"
    assert events[0].payload == {"step": 1}


def test_acquired_lock_releases_only_when_owned():
    class FakeLock:
        def __init__(self, acquired):
            self.acquired = acquired
            self.releases = 0

        def acquire(self):
            return self.acquired

        def release(self):
            if not self.acquired:
                raise AssertionError("unowned lock released")
            self.releases += 1

    class LockRedis(FakeRedis):
        def __init__(self, acquired):
            super().__init__()
            self.fake_lock = FakeLock(acquired)

        def lock(self, name, timeout, blocking_timeout, thread_local=False):
            return self.fake_lock

    owned_client = LockRedis(True)
    runtime = CommitteeRuntime(_settings(), client=owned_client)
    with runtime.acquire_run_lock("alice", "run-1"):
        pass
    assert owned_client.fake_lock.releases == 1

    unowned_client = LockRedis(False)
    runtime = CommitteeRuntime(_settings(), client=unowned_client)
    with pytest.raises(RuntimeLockError):
        with runtime.acquire_run_lock("alice", "run-1"):
            pass
    assert unowned_client.fake_lock.releases == 0


def test_runtime_uses_json_safe_domain_encoding_and_rejects_invalid_values():
    class Choice(str, Enum):
        READY = "ready"

    fake = FakeRedis()
    runtime = CommitteeRuntime(_settings(), client=fake)
    when = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    payload = {
        "when": when,
        "amount": Decimal("1234567890.123456789"),
        "choice": Choice.READY,
    }

    runtime.append_event("alice", "run-1", "progress", payload)
    event = runtime.read_events_after("alice", "run-1")[0]
    assert event.payload == {
        "when": "2026-07-21T12:00:00Z",
        "amount": "1234567890.123456789",
        "choice": "ready",
    }
    runtime.cache_set("result", "1", payload)
    assert runtime.cache_get("result", "1") == event.payload

    with pytest.raises(ValueError, match="finite"):
        runtime.cache_set("result", "nan", {"nested": [float("nan")]})
    with pytest.raises(TypeError, match="unsupported"):
        runtime.append_event("alice", "run-1", "bad", {"value": object()})


def test_expired_lock_does_not_mask_business_exception():
    class ExpiredLock:
        def acquire(self):
            return True

        def release(self):
            raise LockNotOwnedError("lease expired")

    class ExpiredRedis(FakeRedis):
        def lock(self, name, timeout, blocking_timeout, thread_local=False):
            return ExpiredLock()

    runtime = CommitteeRuntime(_settings(), client=ExpiredRedis())

    with pytest.raises(KeyError, match="business"):
        with runtime.acquire_run_lock("alice", "run-1"):
            raise KeyError("business")

    with pytest.raises(RuntimeLockError, match="expired"):
        with runtime.acquire_run_lock("alice", "run-1"):
            pass


def test_distributed_lock_disables_thread_local_token_storage():
    class Client:
        def __init__(self):
            self.kwargs = None

        def lock(self, name, **kwargs):
            self.kwargs = kwargs
            return object()

    client = Client()
    CommitteeRuntime(_settings(), client=client).run_lock("u", "r")
    assert client.kwargs["thread_local"] is False


def test_real_redis_lock_semantics_allow_cross_thread_extend_and_reject_wrong_owner():
    class MemoryLock(Lock):
        def register_scripts(self):
            pass

        def do_acquire(self, token):
            if getattr(self, "owner", None) is not None:
                return False
            self.owner = token
            return True

        def do_extend(self, additional_time, replace_ttl):
            if self.owner != self.local.token:
                raise LockNotOwnedError("wrong owner")
            self.extended = (additional_time, replace_ttl)
            return True

        def do_release(self, expected_token):
            if self.owner != expected_token:
                raise LockNotOwnedError("wrong owner")
            self.owner = None

    lock = MemoryLock(
        object(),
        "run",
        timeout=30,
        blocking_timeout=0,
        thread_local=False,
    )
    assert lock.acquire()
    token = lock.local.token
    errors = []

    def renew():
        try:
            lock.extend(30, replace_ttl=True)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=renew)
    thread.start()
    thread.join()
    assert errors == []
    assert lock.extended == (30, True)

    lock.local.token = b"wrong"
    with pytest.raises(LockNotOwnedError):
        lock.extend(30, replace_ttl=True)
    lock.local.token = token
    lock.release()
