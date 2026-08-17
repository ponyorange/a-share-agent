"""Build ChatOpenAI client for a user's per-slot LLM credentials."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from langchain_openai import ChatOpenAI

from ..llm_settings import resolve_llm_credentials

_TIER_SLOT = {"quick": "committee_quick", "deep": "committee_deep"}


def build_chat_model(
    user_id: str,
    *,
    slot: str | None = None,
    temperature: float = 0.3,
    streaming: bool = True,
    tier: Literal["quick", "deep"] | None = None,
    committee_config: Mapping[str, Any] | None = None,
    request_timeout: float | None = None,
) -> ChatOpenAI:
    if tier is not None:
        slot = _TIER_SLOT[tier]
    if not slot:
        raise ValueError("build_chat_model 需要 slot 或 tier")
    creds = resolve_llm_credentials(user_id, slot)
    kwargs: dict[str, Any] = {
        "api_key": creds["api_key"],
        "base_url": creds["base_url"],
        "model": creds["model"],
        "streaming": streaming,
        "stream_usage": streaming,
    }
    if creds.get("provider") != "kimi":
        kwargs["temperature"] = temperature
    if request_timeout is not None:
        kwargs["timeout"] = request_timeout
    return ChatOpenAI(**kwargs)
