"""LangGraph Redis checkpoint construction and optional initialization.

``RedisSaver.setup()`` requires Redis Stack (or a compatible server) with
RedisJSON and RediSearch available. Construction is lazy, while
``initialize_checkpoint_saver`` catches setup failures so an unavailable or
incompatible Redis deployment cannot prevent the main application from
starting.

Committee graphs call async APIs (``aget_state`` / ``astream``). Sync
``RedisSaver`` leaves those methods unimplemented, so production savers are
wrapped with ``AsyncBridgedRedisSaver`` that delegates via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.redis import RedisSaver
from redis import Redis

from .redis_client import (
    CommitteeConfigurationError,
    CommitteeRedisSettings,
    create_client,
    require_enabled,
)


@dataclass(frozen=True, slots=True)
class CheckpointSetupResult:
    enabled: bool
    ok: bool
    status: str
    saver: RedisSaver | Any | None = field(default=None, repr=False)
    error: str | None = None


def _coerce_version(value: Any) -> Any:
    """Normalize Redis-persisted channel versions for LangGraph comparisons.

    ``RedisSaver`` may round-trip ``channel_versions`` as numeric strings while
    ``versions_seen`` stays as ints. LangGraph then evaluates ``versions[chan] >
    seen[chan]`` and raises ``TypeError``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit() or (
            stripped.startswith("-") and stripped[1:].isdigit()
        ):
            return int(stripped)
    return value


def _normalize_channel_versions(versions: Any) -> Any:
    if not isinstance(versions, dict):
        return versions
    return {key: _coerce_version(value) for key, value in versions.items()}


def _normalize_versions_seen(versions_seen: Any) -> Any:
    if not isinstance(versions_seen, dict):
        return versions_seen
    normalized: dict[str, Any] = {}
    for node, mapping in versions_seen.items():
        if isinstance(mapping, dict):
            normalized[node] = _normalize_channel_versions(mapping)
        else:
            normalized[node] = mapping
    return normalized


def _normalize_checkpoint(checkpoint: Checkpoint) -> Checkpoint:
    payload = dict(checkpoint)
    if "channel_versions" in payload:
        payload["channel_versions"] = _normalize_channel_versions(
            payload.get("channel_versions")
        )
    if "versions_seen" in payload:
        payload["versions_seen"] = _normalize_versions_seen(
            payload.get("versions_seen")
        )
    return payload  # type: ignore[return-value]


def _normalize_checkpoint_tuple(
    checkpoint_tuple: CheckpointTuple,
) -> CheckpointTuple:
    return checkpoint_tuple._replace(
        checkpoint=_normalize_checkpoint(checkpoint_tuple.checkpoint)
    )


class AsyncBridgedRedisSaver(BaseCheckpointSaver):
    """Expose sync ``RedisSaver`` methods to LangGraph async graph APIs."""

    def __init__(self, saver: RedisSaver) -> None:
        super().__init__(serde=getattr(saver, "serde", None))
        self._saver = saver

    def __getattr__(self, name: str) -> Any:
        return getattr(self._saver, name)

    def get_tuple(self, config: Any) -> CheckpointTuple | None:
        checkpoint_tuple = self._saver.get_tuple(config)
        if checkpoint_tuple is None:
            return None
        if not hasattr(checkpoint_tuple, "checkpoint") or not hasattr(
            checkpoint_tuple, "_replace"
        ):
            return checkpoint_tuple
        return _normalize_checkpoint_tuple(checkpoint_tuple)

    def put(
        self,
        config: Any,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> Any:
        return self._saver.put(config, checkpoint, metadata, new_versions)

    def put_writes(
        self,
        config: Any,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return self._saver.put_writes(config, writes, task_id, task_path)

    def list(
        self,
        config: Any | None,
        *,
        filter: dict[str, Any] | None = None,
        before: Any | None = None,
        limit: int | None = None,
    ):
        return self._saver.list(
            config, filter=filter, before=before, limit=limit
        )

    def delete_thread(self, thread_id: str) -> None:
        return self._saver.delete_thread(thread_id)

    async def aget_tuple(self, config: Any) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(
        self,
        config: Any,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> Any:
        return await asyncio.to_thread(
            self._saver.put,
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: Any,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._saver.put_writes,
            config,
            writes,
            task_id,
            task_path,
        )

    async def alist(
        self,
        config: Any | None,
        *,
        filter: dict[str, Any] | None = None,
        before: Any | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        items = await asyncio.to_thread(
            lambda: list(
                self._saver.list(
                    config, filter=filter, before=before, limit=limit
                )
            )
        )
        for item in items:
            yield item

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self._saver.delete_thread, thread_id)


def bridge_async_checkpoint_saver(saver: Any) -> Any:
    """Wrap a sync RedisSaver so async graph APIs work."""
    if isinstance(saver, AsyncBridgedRedisSaver):
        return saver
    if isinstance(saver, RedisSaver):
        return AsyncBridgedRedisSaver(saver)
    return saver


def create_checkpoint_saver(
    settings: CommitteeRedisSettings | None = None,
    *,
    client: Redis | Any | None = None,
    saver_class: type[RedisSaver] | Callable[..., Any] = RedisSaver,
) -> RedisSaver | Any:
    """Construct a namespaced saver without running Redis Stack setup."""
    resolved = settings if settings is not None else CommitteeRedisSettings.from_env()
    require_enabled(resolved)
    connection = client if client is not None else create_client(resolved)
    ttl_minutes = max(1, (resolved.result_ttl + 59) // 60)
    saver = saver_class(
        redis_client=connection,
        checkpoint_prefix=resolved.key("checkpoint"),
        checkpoint_write_prefix=resolved.key("checkpoint-write"),
        ttl={"default_ttl": ttl_minutes, "refresh_on_read": False},
    )
    return bridge_async_checkpoint_saver(saver)


def initialize_checkpoint_saver(
    settings: CommitteeRedisSettings | None = None,
    *,
    saver_factory: Callable[..., Any] = create_checkpoint_saver,
) -> CheckpointSetupResult:
    """Run Redis Stack index setup and return a secret-free status."""
    try:
        resolved = (
            settings if settings is not None else CommitteeRedisSettings.from_env()
        )
    except CommitteeConfigurationError as exc:
        return CheckpointSetupResult(
            enabled=True,
            ok=False,
            status="configuration_error",
            error=type(exc).__name__,
        )
    if not resolved.enabled:
        return CheckpointSetupResult(enabled=False, ok=False, status="disabled")

    try:
        saver = saver_factory(resolved)
        saver.setup()
    except Exception as exc:
        message = str(exc).lower()
        stack_markers = (
            "json.set",
            "json.get",
            "ft.create",
            "ft.search",
            "redisjson",
            "redisearch",
            "unknown command",
        )
        return CheckpointSetupResult(
            enabled=True,
            ok=False,
            status=(
                "redis_stack_required"
                if any(marker in message for marker in stack_markers)
                else "unavailable"
            ),
            error=type(exc).__name__,
        )
    return CheckpointSetupResult(
        enabled=True,
        ok=True,
        status="ready",
        saver=saver,
    )
