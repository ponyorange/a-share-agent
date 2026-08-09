from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Action(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


@dataclass
class Node:
    node_id: str
    layer: str
    node_type: str
    label: str = ""
    aliases: set[str] = field(default_factory=set)
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, order=True)
class EdgeKey:
    src: str
    dst: str
    edge_type: str
    owner: str
    scope_id: str


@dataclass
class Edge:
    src: str
    dst: str
    edge_type: str
    owner: str
    scope_id: str
    layer: str
    confidence: float = 0.0
    commits: float = 0.0
    last_tick: int = 0
    sample_count: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> EdgeKey:
        return EdgeKey(
            self.src,
            self.dst,
            self.edge_type,
            self.owner,
            self.scope_id,
        )


@dataclass(frozen=True)
class SignalContext:
    ticker: str
    trade_date: str
    trade_tick: int
    market_regime: str
    industry: str
    patterns: tuple[str, ...] = ()
    horizon_days: int = 5
    owner: str = "default"

    @property
    def scope_id(self) -> str:
        return f"{self.horizon_days}::{self.market_regime}"


@dataclass(frozen=True)
class MarketState:
    ticker: str
    is_suspended: bool = False
    is_limit_up: bool = False
    is_limit_down: bool = False
    is_st: bool = False
    trading_days_since_listing: int = 10_000


@dataclass(frozen=True)
class LearningConfig:
    tau_base: float = 10.0
    tau_per_sample: float = 5.0
    confidence_floor: float = -3.0
    confidence_ceiling: float = 12.0
    initial_confidence: float = 0.25
    shrinkage: float = 5.0
    min_score: float = 0.1
    min_margin: float = 0.05
    neutral_band: float = 0.002
    max_feedback: float = 2.0
    layer_weights: dict[str, float] = field(
        default_factory=lambda: {
            "market": 0.4,
            "industry": 0.7,
            "pattern": 1.0,
            "stock": 1.2,
        }
    )
    feedback_weights: dict[str, float] = field(
        default_factory=lambda: {
            "market": 0.25,
            "industry": 0.5,
            "pattern": 0.8,
            "stock": 1.0,
        }
    )


@dataclass(frozen=True)
class TradingCostConfig:
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    slippage_rate: float = 0.0002


@dataclass(frozen=True)
class EvidenceContribution:
    edge_key: EdgeKey
    layer: str
    action: Action
    decayed_confidence: float
    reliability: float
    contribution: float


@dataclass(frozen=True)
class SignalDecision:
    action: Action
    raw_action: Action
    scores: dict[Action, float]
    margin: float
    evidence: tuple[EvidenceContribution, ...]
    blocked_reason: str | None = None
    prediction_id: str | None = None


@dataclass
class PendingPrediction:
    prediction_id: str
    context: SignalContext
    action: Action
    scores: dict[Action, float]
    evidence: tuple[EvidenceContribution, ...]
    entry_price: float
    benchmark_entry_price: float
    due_tick: int
    status: str = "pending"


@dataclass(frozen=True)
class SettlementResult:
    prediction_id: str
    status: str
    excess_return: float | None
    feedback_delta: float
