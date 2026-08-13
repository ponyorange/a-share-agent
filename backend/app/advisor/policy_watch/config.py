"""Read policy_watch section from advisor YAML with spec defaults."""

from __future__ import annotations

from typing import Any

from ..config_loader import load_config

_DEFAULTS: dict[str, Any] = {
    "max_custom_sources": 8,
    "max_list_links": 20,
    "max_fetch_per_tick": 5,
    "max_sources_per_tick": 4,
    "max_tick_seconds": 8,
    "max_article_chars": 8000,
    "similar_title_hours": 24,
    "interval_trading_min": 5,
    "interval_trading_max": 180,
    "interval_offhours_min": 15,
    "interval_offhours_max": 360,
    "default_interval_trading": 15,
    "default_interval_offhours": 60,
    "trading_start": "09:15",
    "trading_end": "15:05",
    "presets": {},
}


def policy_watch_config() -> dict[str, Any]:
    raw = load_config().get("policy_watch")
    out = dict(_DEFAULTS)
    if isinstance(raw, dict):
        out.update(raw)
    return out
