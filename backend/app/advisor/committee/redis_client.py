"""Lazy, secret-safe Redis configuration shared by API and RQ workers."""

from __future__ import annotations

import os
import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from redis import Connection, ConnectionPool, Redis, SSLConnection


class CommitteeConfigurationError(ValueError):
    """Committee infrastructure configuration is invalid."""


class CommitteeDisabledError(RuntimeError):
    """Committee infrastructure was used while disabled."""


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


class _SecretSafeConnectionRepr:
    """Prevent redis-py connection repr from exposing authentication data."""

    def __repr__(self) -> str:
        password = "***" if getattr(self, "password", None) else None
        return (
            f"<{type(self).__module__}.{type(self).__name__}"
            f"(host={self.host},port={self.port},db={self.db},password={password})>"
        )


class SecretSafeConnection(_SecretSafeConnectionRepr, Connection):
    """Plain Redis connection with a redacted repr."""


class SecretSafeSSLConnection(_SecretSafeConnectionRepr, SSLConnection):
    """TLS Redis connection with a redacted repr."""


class SecretSafeConnectionPool(ConnectionPool):
    """Connection pool whose repr contains only a pre-redacted endpoint."""

    def __init__(self, *args: Any, safe_url: str, **kwargs: Any) -> None:
        self._safe_url = safe_url
        super().__init__(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<{type(self).__module__}.{type(self).__name__}({self._safe_url})>"


def _parse_bool(value: str | None, name: str, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise CommitteeConfigurationError(
        f"{name} must be one of true/false, yes/no, on/off, or 1/0"
    )


def _parse_int(
    value: str | None,
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        parsed = default if value is None or not value.strip() else int(value)
    except (TypeError, ValueError) as exc:
        raise CommitteeConfigurationError(f"{name} must be an integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        limit = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise CommitteeConfigurationError(f"{name} must be {limit}")
    return parsed


def _validate_host(host: str | None, *, enabled: bool) -> str | None:
    if not host:
        if enabled:
            raise CommitteeConfigurationError(
                "REDIS_HOST is required when COMMITTEE_ENABLED is true"
            )
        return None
    if (
        "://" in host
        or any(char in host for char in ("@", "/", "\\", "?", "#"))
        or any(char.isspace() for char in host)
    ):
        raise CommitteeConfigurationError(
            "REDIS_HOST must contain only a hostname or IP address"
        )
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise CommitteeConfigurationError(
                "REDIS_HOST must contain only a hostname or IP address"
            ) from exc
    return host


@dataclass(frozen=True, slots=True)
class CommitteeRedisSettings:
    """Validated settings with secret-free repr and diagnostics."""

    enabled: bool = False
    host: str | None = None
    port: int = 6379
    password: str | None = field(default=None, repr=False)
    ssl: bool = False
    db: int = 0
    queue_name: str = "committee"
    job_timeout: int = 900
    key_prefix: str = "sharedata:committee"
    result_ttl: int = 86400
    failure_ttl: int = 604800
    lock_ttl: int = 300
    socket_timeout: float = 2.0

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> CommitteeRedisSettings:
        env = os.environ if environ is None else environ
        enabled = _parse_bool(env.get("COMMITTEE_ENABLED"), "COMMITTEE_ENABLED")
        host = _validate_host(
            (env.get("REDIS_HOST") or "").strip() or None, enabled=enabled
        )

        queue_name = (env.get("COMMITTEE_QUEUE_NAME") or "committee").strip()
        key_prefix = (env.get("COMMITTEE_KEY_PREFIX") or "sharedata:committee").strip(
            ": "
        )
        if not queue_name:
            raise CommitteeConfigurationError("COMMITTEE_QUEUE_NAME cannot be empty")
        if not key_prefix:
            raise CommitteeConfigurationError("COMMITTEE_KEY_PREFIX cannot be empty")

        return cls(
            enabled=enabled,
            host=host,
            port=_parse_int(
                env.get("REDIS_PORT"), "REDIS_PORT", 6379, minimum=1, maximum=65535
            ),
            password=env.get("REDIS_PASSWORD") or None,
            ssl=_parse_bool(env.get("REDIS_SSL"), "REDIS_SSL"),
            db=_parse_int(env.get("REDIS_DB"), "REDIS_DB", 0, minimum=0),
            queue_name=queue_name,
            job_timeout=_parse_int(
                env.get("COMMITTEE_JOB_TIMEOUT"),
                "COMMITTEE_JOB_TIMEOUT",
                900,
                minimum=1,
            ),
            key_prefix=key_prefix,
            result_ttl=_parse_int(
                env.get("COMMITTEE_RESULT_TTL"),
                "COMMITTEE_RESULT_TTL",
                86400,
                minimum=1,
            ),
            failure_ttl=_parse_int(
                env.get("COMMITTEE_FAILURE_TTL"),
                "COMMITTEE_FAILURE_TTL",
                604800,
                minimum=1,
            ),
            lock_ttl=_parse_int(
                env.get("COMMITTEE_LOCK_TTL"),
                "COMMITTEE_LOCK_TTL",
                300,
                minimum=1,
            ),
        )

    @property
    def safe_url(self) -> str:
        """Return a diagnostic URL that can safely be logged."""
        scheme = "rediss" if self.ssl else "redis"
        credentials = ":***@" if self.password else ""
        host = self.host or "<unset>"
        display_host = f"[{host}]" if ":" in host else host
        return f"{scheme}://{credentials}{display_host}:{self.port}/{self.db}"

    def connection_kwargs(self) -> dict[str, Any]:
        """Return redis-py constructor arguments without opening a socket."""
        return {
            "host": self.host,
            "port": self.port,
            "db": self.db,
            "password": self.password,
            "ssl": self.ssl,
            "decode_responses": False,
            "socket_connect_timeout": self.socket_timeout,
            "socket_timeout": self.socket_timeout,
        }

    def key(self, *parts: str) -> str:
        clean_parts = [str(part).strip(": ") for part in parts]
        if any(not part for part in clean_parts):
            raise ValueError("Redis key parts cannot be empty")
        return ":".join((self.key_prefix, *clean_parts))


def require_enabled(settings: CommitteeRedisSettings) -> None:
    """Raise a secret-free error when committee infrastructure is disabled."""
    if not settings.enabled:
        raise CommitteeDisabledError(
            "Committee infrastructure is disabled; set COMMITTEE_ENABLED=true"
        )


def create_pool(settings: CommitteeRedisSettings | None = None) -> ConnectionPool:
    """Create a lazy connection pool; redis-py connects only on first command."""
    resolved = settings if settings is not None else CommitteeRedisSettings.from_env()
    require_enabled(resolved)
    kwargs = resolved.connection_kwargs()
    kwargs.pop("ssl")
    connection_class = (
        SecretSafeSSLConnection if resolved.ssl else SecretSafeConnection
    )
    return SecretSafeConnectionPool(
        connection_class=connection_class,
        safe_url=resolved.safe_url,
        **kwargs,
    )


def create_client(
    settings: CommitteeRedisSettings | None = None,
    *,
    pool: ConnectionPool | None = None,
) -> Redis:
    """Create a lazy Redis client backed by the shared connection settings."""
    resolved = settings if settings is not None else CommitteeRedisSettings.from_env()
    require_enabled(resolved)
    connection_pool = pool if pool is not None else create_pool(resolved)
    return Redis(connection_pool=connection_pool)


def distributed_lock(
    name: str,
    settings: CommitteeRedisSettings | None = None,
    *,
    client: Redis | Any | None = None,
    blocking_timeout: float = 0,
) -> Any:
    """Construct a namespaced Redis lock with a bounded lease."""
    resolved = settings if settings is not None else CommitteeRedisSettings.from_env()
    require_enabled(resolved)
    connection = client if client is not None else create_client(resolved)
    return connection.lock(
        resolved.key("lock", name),
        timeout=resolved.lock_ttl,
        blocking_timeout=blocking_timeout,
        thread_local=False,
    )


def set_with_ttl(
    namespace: str,
    key: str,
    value: bytes | str,
    settings: CommitteeRedisSettings | None = None,
    *,
    client: Redis | Any | None = None,
) -> bool:
    """Store a namespaced value with the configured mandatory expiration."""
    resolved = settings if settings is not None else CommitteeRedisSettings.from_env()
    require_enabled(resolved)
    connection = client if client is not None else create_client(resolved)
    return bool(
        connection.set(
            resolved.key(namespace, key), value, ex=resolved.result_ttl
        )
    )


def health_check(
    settings: CommitteeRedisSettings | None = None,
    *,
    client: Redis | Any | None = None,
) -> dict[str, Any]:
    """Return a non-throwing status and never include exception messages."""
    try:
        resolved = (
            settings if settings is not None else CommitteeRedisSettings.from_env()
        )
    except CommitteeConfigurationError as exc:
        return {
            "enabled": True,
            "ok": False,
            "status": "configuration_error",
            "error": type(exc).__name__,
        }
    if not resolved.enabled:
        return {"enabled": False, "ok": False, "status": "disabled"}
    try:
        connection = client if client is not None else create_client(resolved)
        connection.ping()
    except Exception as exc:
        return {
            "enabled": True,
            "ok": False,
            "status": "unavailable",
            "endpoint": resolved.safe_url,
            "error": type(exc).__name__,
        }
    return {
        "enabled": True,
        "ok": True,
        "status": "ready",
        "endpoint": resolved.safe_url,
    }
