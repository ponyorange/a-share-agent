"""Multi data-source registry."""

from __future__ import annotations

from typing import Any

from .akshare_provider import AkshareProvider
from .baostock_provider import BaostockProvider
from .base import DataSource
from .tushare_provider import TushareProvider

_PROVIDERS: dict[str, DataSource] = {
    AkshareProvider.id: AkshareProvider(),
    TushareProvider.id: TushareProvider(),
    BaostockProvider.id: BaostockProvider(),
}


def list_sources() -> list[dict[str, Any]]:
    return [p.describe() for p in _PROVIDERS.values()]


def get_provider(source_id: str) -> DataSource:
    key = (source_id or "").strip().lower()
    provider = _PROVIDERS.get(key)
    if provider is None:
        known = ", ".join(_PROVIDERS)
        raise KeyError(f"未知数据源: {source_id}（可选: {known}）")
    return provider


def has_feature(source_id: str, feature: str) -> bool:
    return feature in get_provider(source_id).features
