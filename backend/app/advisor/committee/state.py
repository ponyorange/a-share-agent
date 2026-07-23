"""Typed LangGraph state and deterministic parallel reducers."""

from __future__ import annotations

import operator
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, NotRequired, TypedDict

from pydantic import Field, FiniteFloat

from .models import (
    AnalystReport,
    CommitteeModel,
    DebateTurn,
)


class BudgetLimits(CommitteeModel):
    max_calls: int = Field(default=20, ge=1, le=1000)
    max_tokens: int = Field(default=100_000, ge=1)
    node_timeout_seconds: FiniteFloat = Field(default=60, gt=0)
    total_timeout_seconds: FiniteFloat = Field(default=300, gt=0)


class BudgetUsage(CommitteeModel):
    calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    reserved_tokens: int = Field(default=0, ge=0)
    unknown_token_calls: int = Field(default=0, ge=0)
    elapsed_seconds: FiniteFloat = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    reservations: dict[str, tuple[int, int]] = Field(default_factory=dict)


class CommitteeError(CommitteeModel):
    node: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=500)
    critical: bool


class CommitteeEvent(CommitteeModel):
    event_id: str = Field(min_length=1, max_length=256)
    node: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class ModelCallRecord(CommitteeModel):
    call_id: str = Field(min_length=1, max_length=256)
    role: str = Field(min_length=1, max_length=64)
    model_tier: str = Field(pattern="^(quick|deep)$")
    model_name: str = Field(min_length=1, max_length=256)
    elapsed_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    token_usage_known: bool = False
    tool_names: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    attempt: int = Field(default=1, ge=1, le=2)
    status: str = Field(pattern="^(success|invalid|error|timeout)$")
    error: str | None = Field(default=None, min_length=1, max_length=500)
    cached: bool = False


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _json_model(value: Any, model: type[CommitteeModel]) -> dict[str, Any]:
    return model.model_validate(value).model_dump(mode="json")


def merge_analyst_reports(
    left: Sequence[AnalystReport | Mapping[str, Any]] | None,
    right: Sequence[AnalystReport | Mapping[str, Any]] | AnalystReport | Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Deterministically dedupe checkpoint-decoded reports by analyst role."""
    by_role: dict[str, dict[str, Any]] = {}
    for raw in [*_items(left), *_items(right)]:
        report = _json_model(raw, AnalystReport)
        role = report["role"]
        if role in by_role and by_role[role] != report:
            raise ValueError(f"conflicting analyst role: {role}")
        by_role[role] = report
    order = {"fundamental": 0, "technical": 1, "news": 2, "quant": 3}
    return sorted(by_role.values(), key=lambda item: order[item["role"]])


def merge_debate_turns(
    left: Sequence[DebateTurn | Mapping[str, Any]] | None,
    right: Sequence[DebateTurn | Mapping[str, Any]] | DebateTurn | Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Dedupe turns by sequence/speaker and restore canonical ordering."""
    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for raw in [*_items(left), *_items(right)]:
        turn = _json_model(raw, DebateTurn)
        key = (turn["sequence"], turn["speaker"])
        if key in by_key and by_key[key] != turn:
            raise ValueError(f"conflicting debate turn: {key}")
        by_key[key] = turn
    return [by_key[key] for key in sorted(by_key)]


def _merge_unique(
    left: Sequence[Any] | None,
    right: Sequence[Any] | Any | None,
    *,
    key,
) -> list[Any]:
    by_key: dict[Any, Any] = {}
    for raw in [*_items(left), *_items(right)]:
        item = (
            raw.model_dump(mode="json")
            if hasattr(raw, "model_dump")
            else dict(raw)
            if isinstance(raw, Mapping)
            else raw
        )
        item_key = key(item)
        if item_key in by_key:
            if by_key[item_key] != item:
                raise ValueError(f"conflicting reducer value: {item_key}")
            continue
        by_key[item_key] = item
    return [by_key[item_key] for item_key in sorted(by_key)]


def merge_model_calls(left, right):
    by_id: dict[str, dict[str, Any]] = {}
    for raw in [*_items(left), *_items(right)]:
        item = ModelCallRecord.model_validate(raw).model_dump(mode="json")
        call_id = item["call_id"]
        current = by_id.get(call_id)
        if current is None:
            by_id[call_id] = item
            continue
        stable_current = {
            key: value
            for key, value in current.items()
            if key not in {"cached", "elapsed_ms"}
        }
        stable_item = {
            key: value
            for key, value in item.items()
            if key not in {"cached", "elapsed_ms"}
        }
        if stable_current != stable_item:
            raise ValueError(f"conflicting model call: {call_id}")
        by_id[call_id] = min(
            (current, item),
            key=lambda value: (
                value["cached"],
                -value["elapsed_ms"],
            ),
        )
    return [by_id[key] for key in sorted(by_id)]


def merge_errors(left, right):
    return _merge_unique(
        left,
        right,
        key=lambda item: (item["node"], item["code"], item["message"]),
    )


def merge_events(left, right):
    return _merge_unique(left, right, key=lambda item: item["event_id"])


def merge_budget(
    left: BudgetUsage | Mapping[str, Any] | None,
    right: BudgetUsage | Mapping[str, Any] | None,
) -> dict[str, Any]:
    current = BudgetUsage.model_validate(left or {})
    incoming = BudgetUsage.model_validate(right or {})
    if current.revision != incoming.revision:
        selected = (
            current if current.revision > incoming.revision else incoming
        )
        return selected.model_dump(mode="json")
    reservations = dict(current.reservations)
    for key, value in incoming.reservations.items():
        if key in reservations and reservations[key] != value:
            raise ValueError(f"conflicting budget reservation: {key}")
        reservations[key] = value
    return BudgetUsage(
        calls=max(current.calls, incoming.calls),
        tokens=max(current.tokens, incoming.tokens),
        reserved_tokens=min(
            current.reserved_tokens,
            incoming.reserved_tokens,
        ),
        unknown_token_calls=max(
            current.unknown_token_calls,
            incoming.unknown_token_calls,
        ),
        elapsed_seconds=max(current.elapsed_seconds, incoming.elapsed_seconds),
        revision=current.revision,
        reservations=reservations,
    ).model_dump(mode="json")


def merge_status(left: str | None, right: str | None) -> str:
    rank = {"pending": 0, "running": 1, "completed": 2, "aborted": 3}
    values = [value for value in (left, right) if value is not None]
    return max(values, key=lambda value: rank.get(value, 0)) if values else "pending"


class CommitteeState(TypedDict):
    run_id: str
    user_id: str
    snapshot: NotRequired[dict[str, Any]]
    snapshot_request: NotRequired[dict[str, Any]]
    analyst_reports: Annotated[list[dict[str, Any]], merge_analyst_reports]
    debate_turns: Annotated[list[dict[str, Any]], merge_debate_turns]
    debate_round: int
    max_debate_rounds: int
    started_at_epoch: float
    deadline_at_epoch: float
    trade_proposal: NotRequired[dict[str, Any]]
    trade_proposals: NotRequired[list[dict[str, Any]]]
    backtest_verdict: NotRequired[dict[str, Any]]
    risk_verdict: NotRequired[dict[str, Any]]
    final_decision: NotRequired[dict[str, Any]]
    limits: dict[str, Any]
    budget: Annotated[dict[str, Any], merge_budget]
    model_calls: Annotated[list[dict[str, Any]], merge_model_calls]
    errors: Annotated[list[dict[str, Any]], merge_errors]
    events: Annotated[list[dict[str, Any]], merge_events]
    degraded: Annotated[bool, operator.or_]
    status: Annotated[str, merge_status]
