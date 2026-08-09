import hashlib
import json
from dataclasses import asdict

from .errors import PredictionNotFoundError, PredictionNotMatureError
from .graph import SignalGraph
from .market import calculate_excess_return
from .models import (
    Action,
    EvidenceContribution,
    PendingPrediction,
    SettlementResult,
    SignalContext,
    TradingCostConfig,
)


class PredictionLedger:
    def __init__(self, costs: TradingCostConfig | None = None):
        self.costs = costs or TradingCostConfig()
        self.pending: dict[str, PendingPrediction] = {}
        self.unresolved: dict[str, PendingPrediction] = {}
        self.settled: dict[
            str, tuple[PendingPrediction, SettlementResult]
        ] = {}

    def register(
        self,
        context: SignalContext,
        action: Action,
        scores: dict[Action, float],
        evidence: tuple[EvidenceContribution, ...],
        entry_price: float,
        benchmark_entry_price: float,
    ) -> str:
        payload = {
            "context": asdict(context),
            "action": action.value,
            "scores": {
                candidate.value: scores[candidate] for candidate in Action
            },
            "evidence": [
                {
                    "edge_key": asdict(item.edge_key),
                    "layer": item.layer,
                    "action": item.action.value,
                    "decayed_confidence": item.decayed_confidence,
                    "reliability": item.reliability,
                    "contribution": item.contribution,
                }
                for item in evidence
            ],
            "entry_price": entry_price,
            "benchmark_entry_price": benchmark_entry_price,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        prediction_id = hashlib.sha256(encoded).hexdigest()[:24]
        if (
            prediction_id in self.pending
            or prediction_id in self.unresolved
            or prediction_id in self.settled
        ):
            return prediction_id

        self.pending[prediction_id] = PendingPrediction(
            prediction_id=prediction_id,
            context=context,
            action=action,
            scores=dict(scores),
            evidence=evidence,
            entry_price=entry_price,
            benchmark_entry_price=benchmark_entry_price,
            due_tick=context.trade_tick + context.horizon_days,
        )
        return prediction_id

    def require_open(self, prediction_id: str) -> PendingPrediction:
        prediction = self.pending.get(prediction_id)
        if prediction is None:
            prediction = self.unresolved.get(prediction_id)
        if prediction is None:
            raise PredictionNotFoundError(prediction_id)
        return prediction

    def move_to_unresolved(self, prediction_id: str) -> PendingPrediction:
        if prediction_id in self.unresolved:
            return self.unresolved[prediction_id]
        prediction = self.pending.pop(prediction_id, None)
        if prediction is None:
            raise PredictionNotFoundError(prediction_id)
        prediction.status = "unresolved"
        self.unresolved[prediction_id] = prediction
        return prediction

    def finish(
        self,
        prediction: PendingPrediction,
        result: SettlementResult,
    ) -> None:
        self.pending.pop(prediction.prediction_id, None)
        self.unresolved.pop(prediction.prediction_id, None)
        prediction.status = "settled"
        self.settled[prediction.prediction_id] = (prediction, result)


class FeedbackEngine:
    def __init__(
        self,
        graph: SignalGraph,
        ledger: PredictionLedger,
        costs: TradingCostConfig | None = None,
    ):
        self.graph = graph
        self.ledger = ledger
        self.costs = costs or ledger.costs

    def settle(
        self,
        prediction_id: str,
        current_tick: int,
        stock_exit: float,
        benchmark_exit: float,
    ) -> SettlementResult:
        settled = self.ledger.settled.get(prediction_id)
        if settled is not None:
            return settled[1]

        prediction = self.ledger.require_open(prediction_id)
        if current_tick < prediction.due_tick:
            raise PredictionNotMatureError(prediction_id)

        zero_costs = TradingCostConfig(
            commission_rate=0.0,
            stamp_duty_rate=0.0,
            slippage_rate=0.0,
        )
        raw_excess = calculate_excess_return(
            stock_entry=prediction.entry_price,
            stock_exit=stock_exit,
            benchmark_entry=prediction.benchmark_entry_price,
            benchmark_exit=benchmark_exit,
            action=Action.BUY,
            costs=zero_costs,
        )
        realized_action = self._realized_action(raw_excess)
        decision_excess = calculate_excess_return(
            stock_entry=prediction.entry_price,
            stock_exit=stock_exit,
            benchmark_entry=prediction.benchmark_entry_price,
            benchmark_exit=benchmark_exit,
            action=prediction.action,
            costs=self.costs,
        )

        magnitude = self._magnitude(raw_excess)
        correct = prediction.action is realized_action
        feedback_delta = magnitude if correct else -magnitude
        by_axis: dict[
            tuple[str, str, str, str], dict[Action, object]
        ] = {}
        for item in prediction.evidence:
            axis_key = (
                item.edge_key.src,
                item.edge_key.owner,
                item.edge_key.scope_id,
                item.layer,
            )
            by_axis.setdefault(axis_key, {})[item.action] = item.edge_key

        for actions in by_axis.values():
            realized_key = actions[realized_action]
            layer = self.graph.get_edge(realized_key).layer
            weighted_magnitude = (
                magnitude * self.graph.config.feedback_weights[layer]
            )
            self.graph.commit_decision(
                realized_key,
                tick=current_tick,
                reward=weighted_magnitude,
                penalize=actions[prediction.action] if not correct else None,
                penalty=weighted_magnitude,
            )

        result = SettlementResult(
            prediction_id=prediction_id,
            status="settled",
            excess_return=decision_excess,
            feedback_delta=feedback_delta,
        )
        self.ledger.finish(prediction, result)
        return result

    def mark_unresolved(self, prediction_id: str) -> SettlementResult:
        settled = self.ledger.settled.get(prediction_id)
        if settled is not None:
            return settled[1]
        self.ledger.move_to_unresolved(prediction_id)
        return SettlementResult(
            prediction_id=prediction_id,
            status="unresolved",
            excess_return=None,
            feedback_delta=0.0,
        )

    def _realized_action(self, raw_excess: float) -> Action:
        transaction_cost = (
            2 * self.costs.commission_rate
            + self.costs.stamp_duty_rate
            + 2 * self.costs.slippage_rate
        )
        threshold = self.graph.config.neutral_band + transaction_cost
        if raw_excess > threshold:
            return Action.BUY
        if raw_excess < -threshold:
            return Action.SELL
        return Action.HOLD

    def _magnitude(self, raw_excess: float) -> float:
        return min(
            self.graph.config.max_feedback,
            max(0.25, abs(raw_excess) * 100.0),
        )
