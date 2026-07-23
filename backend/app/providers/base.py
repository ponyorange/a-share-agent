"""Data source contract."""

from __future__ import annotations

from typing import Any, Protocol


class DataSource(Protocol):
    id: str
    label: str
    features: tuple[str, ...]

    def describe(self) -> dict[str, Any]: ...

    def health(self) -> dict[str, Any]: ...

    def get_categories(self) -> list[dict[str, Any]]: ...

    def list_interfaces(
        self, category: str | None = None, keyword: str | None = None
    ) -> list[dict[str, Any]]: ...

    def get_interface(self, name: str) -> dict[str, Any] | None: ...

    def fetch(
        self, name: str, params: dict[str, Any], limit: int
    ) -> dict[str, Any]: ...
