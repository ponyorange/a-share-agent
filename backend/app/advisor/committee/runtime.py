"""Ephemeral Redis runtime helpers; Mongo remains the permanent audit source."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from typing import Any, Iterator
from urllib.parse import quote

from redis.exceptions import LockNotOwnedError

from .models import deep_freeze, utc_now
from .redis_client import (
    CommitteeRedisSettings,
    create_client,
    distributed_lock,
    require_enabled,
)
from .repository import encode_api


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: str
    event_type: str
    payload: Any
    created_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", deep_freeze(self.payload))


class RuntimeLockError(RuntimeError):
    pass


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _field(fields: dict[Any, Any], name: str, default: Any = None) -> Any:
    if name in fields:
        return fields[name]
    encoded = name.encode()
    return fields.get(encoded, default)


def _part(value: str) -> str:
    if not value:
        raise ValueError("Redis key part cannot be empty")
    return quote(value, safe="-_.~")


def _json_payload(value: Any) -> tuple[Any, str]:
    encoded = encode_api(value)
    return encoded, json.dumps(
        encoded,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class CommitteeRuntime:
    """Namespaced streams, locks and expiring cache over an injected Redis client."""

    def __init__(
        self,
        settings: CommitteeRedisSettings,
        *,
        client: Any | None = None,
        stream_maxlen: int = 2000,
    ) -> None:
        if stream_maxlen < 1:
            raise ValueError("stream_maxlen must be positive")
        self.settings = settings
        self._injected_client = client
        self.stream_maxlen = stream_maxlen

    def _connection(self) -> Any:
        require_enabled(self.settings)
        if self._injected_client is not None:
            return self._injected_client
        return create_client(self.settings)

    def _stream_key(self, user_id: str, run_id: str) -> str:
        return self.settings.key("stream", _part(user_id), _part(run_id))

    def _ephemeral_stream_key(self, user_id: str, run_id: str) -> str:
        return self.settings.key(
            "stream",
            "ephemeral",
            _part(user_id),
            _part(run_id),
        )

    def append_event(
        self,
        user_id: str,
        run_id: str,
        event_type: str,
        payload: Any,
        *,
        event_id: str | None = None,
    ) -> RuntimeEvent:
        connection = self._connection()
        created_at = utc_now().isoformat()
        safe_payload, encoded_payload = _json_payload(payload)
        kwargs: dict[str, Any] = {
            "maxlen": self.stream_maxlen,
            "approximate": True,
        }
        if event_id is not None:
            kwargs["id"] = event_id
        event_id = connection.xadd(
            self._stream_key(user_id, run_id),
            {
                "event_type": event_type,
                "payload": encoded_payload,
                "created_at": created_at,
            },
            **kwargs,
        )
        return RuntimeEvent(
            event_id=_text(event_id),
            event_type=event_type,
            payload=safe_payload,
            created_at=created_at,
        )

    def append_ephemeral_event(
        self,
        user_id: str,
        run_id: str,
        event_type: str,
        payload: Any,
    ) -> RuntimeEvent:
        created_at = utc_now().isoformat()
        safe_payload, encoded_payload = _json_payload(payload)
        connection = self._connection()
        stream_key = self._ephemeral_stream_key(user_id, run_id)
        event_id = connection.xadd(
            stream_key,
            {
                "event_type": event_type,
                "payload": encoded_payload,
                "created_at": created_at,
            },
            maxlen=self.stream_maxlen,
            approximate=True,
        )
        connection.expire(
            stream_key,
            min(self.settings.result_ttl, 3600),
        )
        return RuntimeEvent(
            event_id=_text(event_id),
            event_type=event_type,
            payload=safe_payload,
            created_at=created_at,
        )

    def read_events_after(
        self,
        user_id: str,
        run_id: str,
        *,
        last_event_id: str = "0-0",
        count: int = 100,
        block_ms: int | None = None,
    ) -> list[RuntimeEvent]:
        if count < 1 or count > 1000:
            raise ValueError("count must be between 1 and 1000")
        if block_ms is not None and block_ms < 0:
            raise ValueError("block_ms cannot be negative")
        rows = self._connection().xread(
            {self._stream_key(user_id, run_id): last_event_id or "0-0"},
            count=count,
            block=block_ms,
        )
        events: list[RuntimeEvent] = []
        for _stream, messages in rows:
            for event_id, fields in messages:
                payload_text = _text(_field(fields, "payload", "{}"))
                events.append(
                    RuntimeEvent(
                        event_id=_text(event_id),
                        event_type=_text(_field(fields, "event_type", "message")),
                        payload=json.loads(payload_text),
                        created_at=_text(_field(fields, "created_at"))
                        if _field(fields, "created_at") is not None
                        else None,
                    )
                )
        return events

    def read_ephemeral_events_after(
        self,
        user_id: str,
        run_id: str,
        *,
        last_event_id: str = "$",
        count: int = 100,
        block_ms: int | None = None,
    ) -> list[RuntimeEvent]:
        if count < 1 or count > 1000:
            raise ValueError("count must be between 1 and 1000")
        if block_ms is not None and block_ms < 0:
            raise ValueError("block_ms cannot be negative")
        rows = self._connection().xread(
            {
                self._ephemeral_stream_key(
                    user_id,
                    run_id,
                ): last_event_id or "$"
            },
            count=count,
            block=block_ms,
        )
        events: list[RuntimeEvent] = []
        for _stream, messages in rows:
            for event_id, fields in messages:
                payload_text = _text(_field(fields, "payload", "{}"))
                events.append(
                    RuntimeEvent(
                        event_id=_text(event_id),
                        event_type=_text(
                            _field(fields, "event_type", "message")
                        ),
                        payload=json.loads(payload_text),
                        created_at=_text(_field(fields, "created_at"))
                        if _field(fields, "created_at") is not None
                        else None,
                    )
                )
        return events

    def run_lock(
        self,
        user_id: str,
        run_id: str,
        *,
        blocking_timeout: float = 0,
    ) -> Any:
        connection = self._connection()
        return distributed_lock(
            f"run:{_part(user_id)}:{_part(run_id)}",
            self.settings,
            client=connection,
            blocking_timeout=blocking_timeout,
        )

    @contextmanager
    def acquire_run_lock(
        self,
        user_id: str,
        run_id: str,
        *,
        blocking_timeout: float = 0,
    ) -> Iterator[Any]:
        lock = self.run_lock(
            user_id,
            run_id,
            blocking_timeout=blocking_timeout,
        )
        acquired = bool(lock.acquire())
        if not acquired:
            raise RuntimeLockError("committee run lock could not be acquired")
        body_failed = False
        try:
            yield lock
        except BaseException:
            body_failed = True
            raise
        finally:
            try:
                lock.release()
            except LockNotOwnedError as exc:
                if not body_failed:
                    raise RuntimeLockError(
                        "committee run lock lease expired before release"
                    ) from exc

    def _cache_key(self, namespace: str, key: str) -> str:
        return self.settings.key("cache", _part(namespace), _part(key))

    def cache_set(
        self,
        namespace: str,
        key: str,
        value: Any,
        *,
        ttl: int | None = None,
    ) -> bool:
        resolved_ttl = self.settings.result_ttl if ttl is None else int(ttl)
        if resolved_ttl < 1:
            raise ValueError("cache ttl must be positive")
        _safe_value, encoded = _json_payload(value)
        return bool(
            self._connection().set(
                self._cache_key(namespace, key),
                encoded,
                ex=resolved_ttl,
            )
        )

    def cache_get(self, namespace: str, key: str) -> Any | None:
        value = self._connection().get(self._cache_key(namespace, key))
        if value is None:
            return None
        return json.loads(_text(value))

    def cache_delete(self, namespace: str, key: str) -> bool:
        return bool(self._connection().delete(self._cache_key(namespace, key)))

    def request_cancel(self, user_id: str, run_id: str) -> bool:
        return self.cache_set(
            "cancel",
            f"{_part(user_id)}:{_part(run_id)}",
            {"requested": True},
            ttl=self.settings.failure_ttl,
        )

    def is_cancel_requested(self, user_id: str, run_id: str) -> bool:
        value = self.cache_get(
            "cancel",
            f"{_part(user_id)}:{_part(run_id)}",
        )
        return bool(value and value.get("requested"))
