"""Build ChatOpenAI client for a user's DeepSeek credentials."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from langchain_openai import ChatOpenAI

from ..config_loader import default_config, load_config
from ..llm_settings import resolve_llm_credentials


def build_chat_model(
    user_id: str,
    *,
    temperature: float = 0.3,
    streaming: bool = True,
    tier: Literal["quick", "deep"] | None = None,
    committee_config: Mapping[str, Any] | None = None,
    request_timeout: float | None = None,
) -> ChatOpenAI:
    """Build the existing user model, optionally selecting a committee tier."""
    creds = resolve_llm_credentials(user_id)
    model_name = creds["model"]
    if tier is not None:
        if committee_config is None:
            effective = load_config().get("committee")
            if not isinstance(effective, Mapping):
                effective = default_config().get("committee", {})
            committee_config = effective
        configured_models = (
            committee_config.get("models", {})
            if committee_config is not None
            else {}
        )
        if isinstance(configured_models, Mapping):
            model_name = str(
                configured_models.get(tier)
                or creds.get(f"{tier}_model")
                or model_name
            )
    kwargs: dict[str, Any] = {
        "api_key": creds["api_key"],
        "base_url": creds["base_url"],
        "model": model_name,
        "temperature": temperature,
        "streaming": streaming,
        "stream_usage": streaming,
    }
    if request_timeout is not None:
        kwargs["timeout"] = request_timeout
    return ChatOpenAI(
        **kwargs,
    )
