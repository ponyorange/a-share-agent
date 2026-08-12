"""Per-turn quotas and config for agent web tools."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Literal

from ..config_loader import load_config

WebKind = Literal["web_research", "web_search", "fetch_url"]

DEFAULT_AGENT_WEB: dict[str, Any] = {
    "web_research": {
        "model": "deepseek-v4-flash",
        "anthropic_base_url": "https://api.deepseek.com/anthropic",
        "server_tool_type": "web_search_20250305",
        "max_tokens": 8192,
        "timeout_seconds": 120,
        "max_query_chars": 500,
        "max_calls_per_turn": 3,
    },
    "web_search": {
        "max_results_default": 5,
        "max_results_cap": 10,
        "max_calls_per_turn": 5,
        "validate_query": "ping",
    },
    "fetch_url": {
        "timeout_seconds": 20,
        "max_bytes": 524288,
        "max_text_chars": 80000,
        "max_redirects": 3,
        "allowed_ports": [80, 443],
        "max_calls_per_turn": 8,
        "escalation": {
            "enabled": True,
            "min_text_chars": 200,
            "max_total_seconds": 90,
            "l2_timeout_seconds": 30,
            "l3_timeout_seconds": 60,
            "enable_stealth": True,
            "solve_cloudflare": True,
            "headless": True,
            "block_patterns": [
                "just a moment",
                "cf-browser-verification",
                "attention required",
                "access denied",
                "verify you are human",
                "checking your browser",
            ],
        },
    },
}

_COUNTERS: ContextVar[dict[str, int] | None] = ContextVar(
    "advisor_web_turn_counters", default=None
)


def get_agent_web_config() -> dict[str, Any]:
    raw = load_config().get("agent_web")
    if not isinstance(raw, dict):
        return dict(DEFAULT_AGENT_WEB)
    out: dict[str, Any] = {}
    for key, default_section in DEFAULT_AGENT_WEB.items():
        section = raw.get(key)
        if isinstance(section, dict):
            merged = dict(default_section)
            merged.update(section)
            out[key] = merged
        else:
            out[key] = dict(default_section)
    return out


def reset_web_turn_counters() -> None:
    _COUNTERS.set({"web_research": 0, "web_search": 0, "fetch_url": 0})


def consume_web_quota(kind: WebKind) -> str | None:
    counters = _COUNTERS.get()
    if counters is None:
        counters = {"web_research": 0, "web_search": 0, "fetch_url": 0}
        _COUNTERS.set(counters)
    cfg = get_agent_web_config().get(kind) or {}
    max_calls = int(cfg.get("max_calls_per_turn") or 0)
    used = int(counters.get(kind) or 0)
    if max_calls > 0 and used >= max_calls:
        return "已达本轮调用上限"
    counters[kind] = used + 1
    return None
