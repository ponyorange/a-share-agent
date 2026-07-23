"""Strong, JSON-safe domain models for an advisor committee run."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_serializer,
    field_validator,
    model_validator,
)


NonEmptyStr = Annotated[str, Field(min_length=1, max_length=256)]
UnitFloat = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]
KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


class FrozenDict(Mapping[KeyT, ValueT], Generic[KeyT, ValueT]):
    """A copied, immutable mapping suitable for trusted model state."""

    __slots__ = ("_data",)

    def __init__(self, value: Mapping[KeyT, ValueT]) -> None:
        self._data = dict(value)

    def __getitem__(self, key: KeyT) -> ValueT:
        return self._data[key]

    def __iter__(self) -> Iterator[KeyT]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FrozenDict({self._data!r})"


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict(
            {key: deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(item) for item in value)
    return value


def deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [deep_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [deep_thaw(item) for item in value]
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PENDING = "pending"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    DEBATING = "debating"
    PROPOSING = "proposing"
    BACKTESTING = "backtesting"
    RISK_REVIEW = "risk_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Horizon(str, Enum):
    NEXT_DAY = "next_day"
    NEXT_WEEK = "next_week"
    NEXT_MONTH = "next_month"


class AnalystRole(str, Enum):
    FUNDAMENTAL = "fundamental"
    TECHNICAL = "technical"
    NEWS = "news"
    QUANT = "quant"
    BULL = "bull"
    BEAR = "bear"


class TradeDirection(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    DEGRADED = "degraded"
    ERROR = "error"


class VerdictStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


class CommitteeModel(BaseModel):
    """Shared validation for immutable committee artifacts."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        str_strip_whitespace=True,
    )

    schema_version: Annotated[int, Field(ge=1)] = 1

    @field_validator("*", mode="before")
    @classmethod
    def reject_non_finite(cls, value: Any) -> Any:
        def check(item: Any) -> None:
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("NaN and infinity are not allowed")
            if isinstance(item, Mapping):
                for nested in item.values():
                    check(nested)
            elif isinstance(item, (list, tuple, set, frozenset)):
                for nested in item:
                    check(nested)

        check(value)
        return value

    @field_validator(
        "created_at",
        "updated_at",
        "as_of",
        "started_at",
        "completed_at",
        "captured_at",
        "data_as_of",
        "expires_at",
        "strategy_template_as_of",
        "job_heartbeat_at",
        "job_deadline_at",
        "execution_lease_expires_at",
        "execution_heartbeat_at",
        "next_resume_at",
        "deleted_at",
        mode="after",
        check_fields=False,
    )
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware UTC")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("datetime must use UTC")
        return value.astimezone(timezone.utc)


class RunScopedModel(CommitteeModel):
    user_id: NonEmptyStr
    run_id: NonEmptyStr


class CommitteeRun(RunScopedModel):
    status: RunStatus = RunStatus.PENDING
    version: Annotated[int, Field(ge=1)] = 1
    strategy_version: NonEmptyStr
    horizon: Horizon = Horizon.NEXT_DAY
    universe: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1, max_length=1000)]
    as_of: datetime
    snapshot_id: Annotated[str | None, Field(min_length=64, max_length=64)] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    error_message: Annotated[str | None, Field(min_length=1, max_length=1000)] = None
    idempotency_key: Annotated[
        str | None, Field(min_length=1, max_length=256)
    ] = None
    request_hash: Annotated[
        str | None, Field(min_length=64, max_length=64)
    ] = None
    queue_job_id: Annotated[
        str | None, Field(min_length=1, max_length=256)
    ] = None
    parent_run_id: Annotated[
        str | None, Field(min_length=1, max_length=256)
    ] = None
    attempt: Annotated[int, Field(ge=1)] = 1
    cancel_requested: bool = False
    initial_input: dict[str, Any] = Field(default_factory=dict)
    job_heartbeat_at: datetime | None = None
    job_deadline_at: datetime | None = None
    next_attempt: Annotated[int, Field(ge=2)] = 2
    execution_owner: str | None = Field(default=None, max_length=256)
    execution_lease_expires_at: datetime | None = None
    execution_heartbeat_at: datetime | None = None
    resume_attempts: Annotated[int, Field(ge=0)] = 0
    next_resume_at: datetime | None = None
    deleted_at: datetime | None = None
    deleted_by: Annotated[
        str | None, Field(min_length=1, max_length=256)
    ] = None

    @model_validator(mode="after")
    def validate_state_invariants(self) -> CommitteeRun:
        terminal = {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        snapshot_states = {
            RunStatus.ANALYZING,
            RunStatus.DEBATING,
            RunStatus.PROPOSING,
            RunStatus.BACKTESTING,
            RunStatus.RISK_REVIEW,
            RunStatus.COMPLETED,
        }
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        if self.completed_at is not None:
            lower_bound = self.started_at or self.created_at
            if self.completed_at < lower_bound:
                raise ValueError("completed_at cannot precede run start")
        unstarted = self.status in {
            RunStatus.CREATED,
            RunStatus.PENDING,
        } or (
            self.status is RunStatus.QUEUED and self.resume_attempts == 0
        )
        if unstarted and self.started_at is not None:
            raise ValueError("unstarted run cannot have started_at")
        if unstarted and self.snapshot_id is not None:
            raise ValueError("unstarted run cannot have snapshot_id")
        if self.status in ACTIVE_STATUSES_REQUIRING_START and self.started_at is None:
            raise ValueError("active run requires started_at")
        if self.status in snapshot_states and self.snapshot_id is None:
            raise ValueError("status requires snapshot_id")
        if self.status in terminal and self.completed_at is None:
            raise ValueError("terminal run requires completed_at")
        if self.status not in terminal and self.completed_at is not None:
            raise ValueError("non-terminal run cannot have completed_at")
        if self.status is RunStatus.FAILED:
            if not self.error_code or not self.error_message:
                raise ValueError("failed run requires error_code and error_message")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("only failed runs may contain error details")
        if self.status is RunStatus.COMPLETED and self.started_at is None:
            raise ValueError("completed run requires started_at")
        if (self.deleted_at is None) != (self.deleted_by is None):
            raise ValueError("deleted_at and deleted_by must be set together")
        if self.deleted_at is not None:
            if self.status not in terminal:
                raise ValueError("only terminal runs may be deleted")
            if self.deleted_at < self.completed_at:
                raise ValueError("deleted_at cannot precede completion")
        return self


ACTIVE_STATUSES_REQUIRING_START = frozenset(
    {
        RunStatus.RUNNING,
        RunStatus.COLLECTING,
        RunStatus.ANALYZING,
        RunStatus.DEBATING,
        RunStatus.PROPOSING,
        RunStatus.BACKTESTING,
        RunStatus.RISK_REVIEW,
    }
)


class EvidenceRef(RunScopedModel):
    evidence_id: NonEmptyStr
    source: NonEmptyStr
    captured_at: datetime
    data_as_of: datetime | None = None
    freshness: Freshness = Freshness.FRESH
    degraded: bool = False
    error: Annotated[str | None, Field(min_length=1, max_length=256)] = None
    uri: Annotated[str | None, Field(min_length=1, max_length=2048)] = None
    content_hash: Annotated[str | None, Field(min_length=64, max_length=64)] = None


class AnalystReport(RunScopedModel):
    role: AnalystRole
    thesis: Annotated[str, Field(min_length=1, max_length=12000)]
    confidence: UnitFloat
    evidence: tuple[EvidenceRef, ...] = ()
    symbols: tuple[NonEmptyStr, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class DebateTurn(RunScopedModel):
    sequence: Annotated[int, Field(ge=1)]
    speaker: AnalystRole
    argument: Annotated[str, Field(min_length=1, max_length=12000)]
    confidence: UnitFloat
    evidence_ids: tuple[NonEmptyStr, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class TradeProposal(RunScopedModel):
    strategy_id: NonEmptyStr | None = None
    strategy_version: NonEmptyStr | None = None
    symbol: NonEmptyStr
    direction: TradeDirection
    target_weight: UnitFloat
    confidence: UnitFloat
    rationale: Annotated[str, Field(min_length=1, max_length=12000)]
    evidence_refs: tuple[EvidenceRef, ...] = ()
    order_type: Literal["market", "limit", "stop_limit"] = "market"
    time_in_force: Literal["day", "gtc"] = "day"
    limit_price: Annotated[FiniteFloat | None, Field(gt=0)] = None
    stop_price: Annotated[FiniteFloat | None, Field(gt=0)] = None
    expires_at: datetime | None = None
    strategy_template_as_of: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_evidence_scope(self) -> TradeProposal:
        if any(
            ref.user_id != self.user_id or ref.run_id != self.run_id
            for ref in self.evidence_refs
        ):
            raise ValueError("proposal evidence must belong to the same run")
        if self.order_type in {"limit", "stop_limit"} and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.order_type == "stop_limit" and self.stop_price is None:
            raise ValueError("stop_limit order requires stop_price")
        return self


class BacktestVerdict(RunScopedModel):
    passed: bool
    score: UnitFloat
    metrics: Mapping[str, Any] = Field(default_factory=lambda: FrozenDict({}))
    summary: Annotated[str, Field(min_length=1, max_length=12000)]
    proposal_hash: Annotated[str, Field(min_length=64, max_length=64)]
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("metrics", mode="after")
    @classmethod
    def freeze_metrics(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return deep_freeze(value)

    @field_serializer("metrics")
    def serialize_metrics(self, value: Mapping[str, Any]) -> Any:
        return deep_thaw(value)

    @property
    def approved(self) -> bool:
        """Explicit committee-facing alias; ``passed`` remains wire-compatible."""
        return self.passed


class RiskRuleResult(CommitteeModel):
    rule_id: NonEmptyStr
    observed: Any
    limit: Any
    severity: Literal["pass", "hard"]
    message: Annotated[str, Field(min_length=1, max_length=2000)]


class RiskVerdict(RunScopedModel):
    status: VerdictStatus
    max_position: UnitFloat
    approved_weight: UnitFloat
    confidence: UnitFloat
    reasons: tuple[Annotated[str, Field(min_length=1, max_length=2000)], ...] = ()
    rules: tuple[RiskRuleResult, ...] = ()
    proposal_hash: Annotated[str, Field(min_length=64, max_length=64)]
    created_at: datetime = Field(default_factory=utc_now)


class FinalDecision(RunScopedModel):
    action: TradeDirection
    symbol: NonEmptyStr
    target_weight: UnitFloat
    confidence: UnitFloat
    rationale: Annotated[str, Field(min_length=1, max_length=12000)]
    risk_status: VerdictStatus
    evidence_refs: tuple[EvidenceRef, ...] = ()
    proposals: tuple[TradeProposal, ...] = ()
    orders: tuple[TradeProposal, ...] = ()
    proposal_hash: Annotated[
        str | None, Field(min_length=64, max_length=64)
    ] = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_evidence_scope(self) -> FinalDecision:
        if any(
            ref.user_id != self.user_id or ref.run_id != self.run_id
            for ref in self.evidence_refs
        ):
            raise ValueError("decision evidence must belong to the same run")
        artifacts = (*self.proposals, *self.orders)
        if any(
            item.user_id != self.user_id or item.run_id != self.run_id
            for item in artifacts
        ):
            raise ValueError("decision proposals must belong to the same run")
        if self.orders and not self.proposals:
            raise ValueError("decision orders require reviewed proposals")
        if self.orders and tuple(self.orders) != tuple(self.proposals):
            raise ValueError("approved orders must exactly match reviewed proposals")
        if self.orders and (
            self.symbol != self.orders[0].symbol
            or self.action is not self.orders[0].direction
            or self.target_weight != self.orders[0].target_weight
        ):
            raise ValueError("primary decision must match first locked order")
        return self
