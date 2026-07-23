"""Read-only, role-scoped views over one frozen market snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_serializer, field_validator

from .models import CommitteeModel, deep_freeze, deep_thaw
from .snapshot import MarketSnapshot


class EvidenceView(CommitteeModel):
    evidence_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    data_as_of: datetime | None = None
    freshness: str
    degraded: bool
    content: Any

    @field_validator("content", mode="before")
    @classmethod
    def freeze_content(cls, value: Any) -> Any:
        return deep_freeze(value)

    @field_serializer("content")
    def serialize_content(self, value: Any) -> Any:
        return deep_thaw(value)


class SnapshotView(CommitteeModel):
    snapshot_id: str
    as_of: datetime
    universe: tuple[str, ...]
    evidence: tuple[EvidenceView, ...]

    @property
    def evidence_ids(self) -> frozenset[str]:
        return frozenset(item.evidence_id for item in self.evidence)

    def prompt_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


ROLE_ITEMS = {
    "fundamental": frozenset({"fundamentals", "market"}),
    "technical": frozenset({"kline", "market"}),
    "news": frozenset({"news", "market"}),
    "quant": frozenset({"kline", "market"}),
}


def snapshot_view(snapshot: MarketSnapshot, role: str) -> SnapshotView:
    """Return copied evidence only; this module exposes no collection or writes."""
    allowed = ROLE_ITEMS.get(role)
    if allowed is None:
        allowed = frozenset(item.name for item in snapshot.items)
    evidence = tuple(
        EvidenceView(
            evidence_id=f"{snapshot.snapshot_id}:{item.name}",
            name=item.name,
            source=item.source,
            data_as_of=item.data_as_of,
            freshness=item.freshness.value,
            degraded=item.degraded,
            content=deep_thaw(item.content),
        )
        for item in snapshot.items
        if item.name in allowed
    )
    return SnapshotView(
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        universe=snapshot.universe,
        evidence=evidence,
    )
