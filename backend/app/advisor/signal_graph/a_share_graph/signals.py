import math

from .errors import InvalidMarketDataError
from .feedback import PredictionLedger
from .graph import SignalGraph
from .market import assess_tradability, normalize_ticker
from .models import (
    Action,
    EvidenceContribution,
    MarketState,
    SignalContext,
    SignalDecision,
)

_ACTION_ORDER = {
    Action.HOLD: 0,
    Action.BUY: 1,
    Action.SELL: 2,
}


class SignalEngine:
    def __init__(self, graph: SignalGraph, ledger: PredictionLedger):
        self.graph = graph
        self.ledger = ledger

    def generate(
        self,
        context: SignalContext,
        market_state: MarketState,
        *,
        entry_price: float,
        benchmark_entry_price: float,
    ) -> SignalDecision:
        self._validate_input(
            context,
            market_state,
            entry_price,
            benchmark_entry_price,
        )

        scores = {action: 0.0 for action in Action}
        evidence = []
        for key in self.graph.ensure_context_edges(context):
            edge = self.graph.get_edge(key)
            action = Action(edge.dst.split(":", 1)[1])
            decayed = self.graph.confidence_now(edge)
            if edge.sample_count <= 0:
                reliability = 0.0
            else:
                reliability = edge.sample_count / (
                    edge.sample_count + self.graph.config.shrinkage
                )
            contribution = (
                decayed
                * self.graph.config.layer_weights[edge.layer]
                * reliability
            )
            scores[action] += contribution
            evidence.append(
                EvidenceContribution(
                    edge_key=key,
                    layer=edge.layer,
                    action=action,
                    decayed_confidence=decayed,
                    reliability=reliability,
                    contribution=contribution,
                )
            )

        ranked = sorted(
            Action,
            key=lambda action: (-scores[action], _ACTION_ORDER[action]),
        )
        raw_action = ranked[0]
        margin = scores[ranked[0]] - scores[ranked[1]]
        action = raw_action
        if (
            scores[raw_action] < self.graph.config.min_score
            or margin < self.graph.config.min_margin
        ):
            action = Action.HOLD

        blocked_reason = assess_tradability(market_state, action)
        evidence_tuple = tuple(evidence)
        if blocked_reason is not None:
            return SignalDecision(
                action=Action.HOLD,
                raw_action=raw_action,
                scores=scores,
                margin=margin,
                evidence=evidence_tuple,
                blocked_reason=blocked_reason,
                prediction_id=None,
            )

        prediction_id = self.ledger.register(
            context,
            action,
            scores,
            evidence_tuple,
            entry_price,
            benchmark_entry_price,
        )
        return SignalDecision(
            action=action,
            raw_action=raw_action,
            scores=scores,
            margin=margin,
            evidence=evidence_tuple,
            prediction_id=prediction_id,
        )

    @staticmethod
    def _validate_input(
        context: SignalContext,
        market_state: MarketState,
        entry_price: float,
        benchmark_entry_price: float,
    ) -> None:
        context_ticker = normalize_ticker(context.ticker)
        state_ticker = normalize_ticker(market_state.ticker)
        if context.ticker != context_ticker:
            raise InvalidMarketDataError(
                f"context ticker must be canonical: {context_ticker}"
            )
        if state_ticker != context_ticker:
            raise InvalidMarketDataError("market state ticker does not match context")
        if (
            not math.isfinite(entry_price)
            or entry_price <= 0
            or not math.isfinite(benchmark_entry_price)
            or benchmark_entry_price <= 0
        ):
            raise InvalidMarketDataError(
                "entry prices must be finite and positive"
            )
        if context.trade_tick < 0:
            raise InvalidMarketDataError("trade_tick must be non-negative")
        if context.horizon_days <= 0:
            raise InvalidMarketDataError("horizon_days must be positive")
