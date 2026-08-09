"""HTTP routes for SignalGraph research and operations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...auth import get_current_user
from . import service as sg


router = APIRouter(prefix="/signal-graph", tags=["advisor-signal-graph"])


def _user(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return user


class BatchBody(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=80)
    trade_date: str | None = None
    persist: bool = True


class SettleBody(BaseModel):
    trade_date: str | None = None
    limit: int = Field(default=200, ge=1, le=2000)


class SyntheticBody(BaseModel):
    seed: int = 7
    days: int = Field(default=60, ge=15, le=400)


@router.get("/summary")
def signal_graph_summary(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    return sg.get_summary()


@router.get("/signal")
def signal_one(
    symbol: str = Query(..., min_length=4, max_length=16),
    trade_date: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    try:
        return sg.generate_signal(symbol, trade_date=trade_date)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/signals")
def signal_batch(
    body: BatchBody,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    if not body.symbols:
        raise HTTPException(status_code=400, detail="symbols 不能为空")
    return sg.generate_signals_batch(
        body.symbols,
        trade_date=body.trade_date,
        persist=body.persist,
    )


@router.post("/settle")
def settle(
    body: SettleBody,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    return sg.settle_due(trade_date=body.trade_date, limit=body.limit)


@router.get("/pending")
def pending(
    limit: int = Query(default=50, ge=1, le=500),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    return sg.list_pending(limit=limit)


@router.get("/settled")
def settled(
    limit: int = Query(default=50, ge=1, le=500),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    return sg.list_settled(limit=limit)


@router.post("/synthetic")
def synthetic(
    body: SyntheticBody,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    try:
        return sg.run_synthetic_demo(seed=body.seed, days=body.days)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
