"""JSON-safe views of SignalGraph decisions and evidence."""

from __future__ import annotations

from typing import Any

from .a_share_graph.models import Action, EvidenceContribution, SignalDecision


def scores_to_dict(scores: dict[Action, float]) -> dict[str, float]:
    return {action.value: float(scores.get(action, 0.0)) for action in Action}


def evidence_to_list(
    evidence: tuple[EvidenceContribution, ...],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in evidence[: max(0, limit)]:
        key = item.edge_key
        rows.append(
            {
                "src": key.src,
                "dst": key.dst,
                "layer": item.layer,
                "action": item.action.value,
                "decayed_confidence": round(float(item.decayed_confidence), 6),
                "reliability": round(float(item.reliability), 6),
                "contribution": round(float(item.contribution), 6),
                "scope_id": key.scope_id,
            }
        )
    return rows


def decision_to_dict(decision: SignalDecision) -> dict[str, Any]:
    return {
        "action": decision.action.value,
        "raw_action": decision.raw_action.value,
        "scores": scores_to_dict(decision.scores),
        "margin": round(float(decision.margin), 6),
        "evidence": evidence_to_list(decision.evidence),
        "blocked_reason": decision.blocked_reason,
        "prediction_id": decision.prediction_id,
    }


def map_graph_action_to_product(
    graph_action: str,
    *,
    has_position: bool,
) -> str:
    """Map BUY/HOLD/SELL onto product actions used by recommendations/advice."""
    action = str(graph_action or "HOLD").upper()
    if has_position:
        if action == "BUY":
            return "add"
        if action == "SELL":
            return "sell"
        return "hold"
    if action == "BUY":
        return "buy"
    if action == "SELL":
        return "watch"
    return "watch"
