"""Load advisor YAML config (with optional per-request override)."""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import context

_CONFIG_PATH = Path(__file__).with_name("config.yaml")


@lru_cache(maxsize=1)
def _load_yaml() -> dict[str, Any]:
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def default_config() -> dict[str, Any]:
    """System default from config.yaml (deep copy)."""
    return copy.deepcopy(_load_yaml())


def load_config() -> dict[str, Any]:
    """Effective config: request-scoped user strategy, else system YAML."""
    override = context.get_config_override()
    if override is not None:
        return override
    return _load_yaml()


def reload_config() -> dict[str, Any]:
    _load_yaml.cache_clear()
    return load_config()
