"""Request-scoped user context for advisor services."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_user_id: ContextVar[str | None] = ContextVar("advisor_user_id", default=None)
_config_override: ContextVar[dict[str, Any] | None] = ContextVar(
    "advisor_config_override", default=None
)


def set_user_id(user_id: str | None) -> None:
    _user_id.set(user_id)


def get_user_id() -> str | None:
    return _user_id.get()


def set_config_override(config: dict[str, Any] | None) -> None:
    """When set, load_config() returns this dict for the current request."""
    _config_override.set(config)


def get_config_override() -> dict[str, Any] | None:
    return _config_override.get()


def bind_user(user_id: str | None) -> None:
    """Set user id and load that user's strategy into config override."""
    set_user_id(user_id)
    if not user_id:
        set_config_override(None)
        return
    # lazy import to avoid cycles
    from .user_strategy import get_user_config

    set_config_override(get_user_config(user_id))
