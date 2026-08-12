"""Pydantic request bodies for paper trader HTTP/API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RiskPatch(BaseModel):
    model_config = {"extra": "forbid"}

    max_single_position: float | None = Field(default=None, gt=0, le=1)
    max_total_exposure: float | None = Field(default=None, gt=0, le=1)
    max_positions: int | None = Field(default=None, ge=1, le=50)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=500)
    max_daily_loss_pct: float | None = Field(default=None, gt=0, le=1)
    lot_size: int | None = Field(default=None, ge=1)
    block_limit_board: bool | None = None


class StartBody(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["signal_first", "llm_first"] | None = None
    interval_sec: int | None = Field(default=None, ge=300, le=900)
    risk: RiskPatch | None = None


class PatchBody(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["signal_first", "llm_first"] | None = None
    interval_sec: int | None = Field(default=None, ge=300, le=900)
    risk: RiskPatch | None = None


class ResumeBody(BaseModel):
    model_config = {"extra": "forbid"}

    confirm_halt_resume: bool = False


def merge_risk(
    base: dict[str, Any], patch: RiskPatch | dict[str, Any] | None
) -> dict[str, Any]:
    out = dict(base or {})
    if patch is None:
        return out
    data = (
        patch.model_dump(exclude_none=True)
        if isinstance(patch, RiskPatch)
        else dict(patch)
    )
    for k, v in data.items():
        if v is not None:
            out[k] = v
    return out
