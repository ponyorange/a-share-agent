import json
import math
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .errors import SnapshotError, SnapshotVersionError
from .feedback import PredictionLedger
from .graph import SignalGraph
from .models import (
    Action,
    Edge,
    EdgeKey,
    EvidenceContribution,
    LearningConfig,
    Node,
    PendingPrediction,
    SettlementResult,
    SignalContext,
    TradingCostConfig,
)

SCHEMA_VERSION = 1


def dump_snapshot(
    graph: SignalGraph,
    ledger: PredictionLedger,
) -> dict[str, Any]:
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "learning_config": asdict(graph.config),
        "trading_cost_config": asdict(ledger.costs),
        "nodes": [
            {
                "node_id": node.node_id,
                "layer": node.layer,
                "node_type": node.node_type,
                "label": node.label,
                "aliases": sorted(node.aliases),
                "attrs": node.attrs,
            }
            for node in sorted(graph.nodes.values(), key=lambda item: item.node_id)
        ],
        "edges": [
            {
                "src": edge.src,
                "dst": edge.dst,
                "edge_type": edge.edge_type,
                "owner": edge.owner,
                "scope_id": edge.scope_id,
                "layer": edge.layer,
                "confidence": edge.confidence,
                "commits": edge.commits,
                "last_tick": edge.last_tick,
                "sample_count": edge.sample_count,
                "attrs": edge.attrs,
            }
            for edge in (
                graph.edges[key] for key in sorted(graph.edges)
            )
        ],
        "axis_ticks": dict(sorted(graph.axis_ticks.items())),
        "context_samples": dict(sorted(graph.context_samples.items())),
        "pending_predictions": [
            _prediction_to_dict(ledger.pending[key])
            for key in sorted(ledger.pending)
        ],
        "unresolved_predictions": [
            _prediction_to_dict(ledger.unresolved[key])
            for key in sorted(ledger.unresolved)
        ],
        "settled_predictions": [
            {
                "prediction": _prediction_to_dict(
                    ledger.settled[key][0]
                ),
                "result": _result_to_dict(ledger.settled[key][1]),
            }
            for key in sorted(ledger.settled)
        ],
    }
    _ensure_jsonable(snapshot)
    return snapshot


def load_snapshot(
    data: dict[str, Any],
) -> tuple[SignalGraph, PredictionLedger]:
    if not isinstance(data, dict):
        raise SnapshotError("snapshot root must be an object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotVersionError(
            f"unsupported schema version: {data.get('schema_version')!r}"
        )

    try:
        config = LearningConfig(**data["learning_config"])
        costs = TradingCostConfig(**data["trading_cost_config"])
    except (KeyError, TypeError, ValueError) as error:
        raise SnapshotError(f"invalid configuration: {error}") from error

    graph = SignalGraph(config)
    ledger = PredictionLedger(costs)
    node_ids = set()
    for raw in _require_list(data, "nodes"):
        try:
            node_id = raw["node_id"]
            if node_id in node_ids:
                raise SnapshotError(f"duplicate node: {node_id}")
            node_ids.add(node_id)
            graph.add_node(
                Node(
                    node_id=node_id,
                    layer=raw["layer"],
                    node_type=raw["node_type"],
                    label=raw.get("label", ""),
                    aliases=set(raw.get("aliases", [])),
                    attrs=dict(raw.get("attrs", {})),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, SnapshotError):
                raise
            raise SnapshotError(f"invalid node: {error}") from error

    edge_keys = set()
    for raw in _require_list(data, "edges"):
        try:
            edge = Edge(
                src=raw["src"],
                dst=raw["dst"],
                edge_type=raw["edge_type"],
                owner=raw["owner"],
                scope_id=raw["scope_id"],
                layer=raw["layer"],
                confidence=float(raw["confidence"]),
                commits=float(raw["commits"]),
                last_tick=int(raw["last_tick"]),
                sample_count=int(raw["sample_count"]),
                attrs=dict(raw.get("attrs", {})),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SnapshotError(f"invalid edge: {error}") from error
        if edge.key in edge_keys:
            raise SnapshotError(f"duplicate edge: {edge.key}")
        edge_keys.add(edge.key)
        if edge.src not in graph.nodes or edge.dst not in graph.nodes:
            raise SnapshotError(f"dangling edge: {edge.key}")
        if not math.isfinite(edge.confidence) or not math.isfinite(edge.commits):
            raise SnapshotError(f"non-finite edge weight: {edge.key}")
        if edge.last_tick < 0 or edge.sample_count < 0:
            raise SnapshotError(f"negative edge counter: {edge.key}")
        graph.add_edge(edge)

    graph.axis_ticks = _load_counter_map(data, "axis_ticks")
    graph.context_samples = _load_counter_map(data, "context_samples")

    seen_predictions = set()
    for raw in _require_list(data, "pending_predictions"):
        prediction = _prediction_from_dict(raw, graph)
        _register_loaded_prediction(
            prediction,
            "pending",
            ledger.pending,
            seen_predictions,
        )
    for raw in _require_list(data, "unresolved_predictions"):
        prediction = _prediction_from_dict(raw, graph)
        _register_loaded_prediction(
            prediction,
            "unresolved",
            ledger.unresolved,
            seen_predictions,
        )
    for raw in _require_list(data, "settled_predictions"):
        try:
            prediction = _prediction_from_dict(raw["prediction"], graph)
            result = _result_from_dict(raw["result"])
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, SnapshotError):
                raise
            raise SnapshotError(f"invalid settled prediction: {error}") from error
        if prediction.prediction_id != result.prediction_id:
            raise SnapshotError("settled prediction/result ID mismatch")
        if prediction.prediction_id in seen_predictions:
            raise SnapshotError(
                f"duplicate prediction: {prediction.prediction_id}"
            )
        seen_predictions.add(prediction.prediction_id)
        prediction.status = "settled"
        ledger.settled[prediction.prediction_id] = (prediction, result)

    return graph, ledger


def save_snapshot(
    path: str | os.PathLike[str],
    graph: SignalGraph,
    ledger: PredictionLedger,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot = dump_snapshot(graph, ledger)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                snapshot,
                stream,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def load_snapshot_file(
    path: str | os.PathLike[str],
) -> tuple[SignalGraph, PredictionLedger]:
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise SnapshotError(f"cannot read snapshot: {error}") from error
    return load_snapshot(data)


def _prediction_to_dict(prediction: PendingPrediction) -> dict[str, Any]:
    return {
        "prediction_id": prediction.prediction_id,
        "context": asdict(prediction.context),
        "action": prediction.action.value,
        "scores": {
            action.value: prediction.scores[action] for action in Action
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
            for item in prediction.evidence
        ],
        "entry_price": prediction.entry_price,
        "benchmark_entry_price": prediction.benchmark_entry_price,
        "due_tick": prediction.due_tick,
        "status": prediction.status,
    }


def _prediction_from_dict(
    raw: dict[str, Any],
    graph: SignalGraph,
) -> PendingPrediction:
    try:
        context_raw = dict(raw["context"])
        context_raw["patterns"] = tuple(context_raw.get("patterns", ()))
        context = SignalContext(**context_raw)
        scores = {
            action: float(raw["scores"][action.value]) for action in Action
        }
        evidence = []
        for item in raw["evidence"]:
            edge_key = EdgeKey(**item["edge_key"])
            if edge_key not in graph.edges:
                raise SnapshotError(
                    f"prediction references missing edge: {edge_key}"
                )
            evidence.append(
                EvidenceContribution(
                    edge_key=edge_key,
                    layer=item["layer"],
                    action=Action(item["action"]),
                    decayed_confidence=float(item["decayed_confidence"]),
                    reliability=float(item["reliability"]),
                    contribution=float(item["contribution"]),
                )
            )
        prediction = PendingPrediction(
            prediction_id=raw["prediction_id"],
            context=context,
            action=Action(raw["action"]),
            scores=scores,
            evidence=tuple(evidence),
            entry_price=float(raw["entry_price"]),
            benchmark_entry_price=float(raw["benchmark_entry_price"]),
            due_tick=int(raw["due_tick"]),
            status=raw["status"],
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, SnapshotError):
            raise
        raise SnapshotError(f"invalid prediction: {error}") from error

    numeric_values = [
        *prediction.scores.values(),
        prediction.entry_price,
        prediction.benchmark_entry_price,
    ]
    for item in prediction.evidence:
        numeric_values.extend(
            [
                item.decayed_confidence,
                item.reliability,
                item.contribution,
            ]
        )
    if any(not math.isfinite(value) for value in numeric_values):
        raise SnapshotError(f"non-finite prediction: {prediction.prediction_id}")
    if (
        prediction.entry_price <= 0
        or prediction.benchmark_entry_price <= 0
        or prediction.due_tick < prediction.context.trade_tick
    ):
        raise SnapshotError(f"invalid prediction counters: {prediction.prediction_id}")
    return prediction


def _result_to_dict(result: SettlementResult) -> dict[str, Any]:
    return asdict(result)


def _result_from_dict(raw: dict[str, Any]) -> SettlementResult:
    result = SettlementResult(
        prediction_id=raw["prediction_id"],
        status=raw["status"],
        excess_return=(
            None
            if raw.get("excess_return") is None
            else float(raw["excess_return"])
        ),
        feedback_delta=float(raw["feedback_delta"]),
    )
    values = [result.feedback_delta]
    if result.excess_return is not None:
        values.append(result.excess_return)
    if result.status != "settled" or any(
        not math.isfinite(value) for value in values
    ):
        raise SnapshotError(f"invalid settlement: {result.prediction_id}")
    return result


def _register_loaded_prediction(
    prediction: PendingPrediction,
    expected_status: str,
    destination: dict[str, PendingPrediction],
    seen: set[str],
) -> None:
    if prediction.status != expected_status:
        raise SnapshotError(
            f"prediction {prediction.prediction_id} has status "
            f"{prediction.status!r}, expected {expected_status!r}"
        )
    if prediction.prediction_id in seen:
        raise SnapshotError(f"duplicate prediction: {prediction.prediction_id}")
    seen.add(prediction.prediction_id)
    destination[prediction.prediction_id] = prediction


def _load_counter_map(data: dict[str, Any], key: str) -> dict[str, int]:
    raw = data.get(key)
    if not isinstance(raw, dict):
        raise SnapshotError(f"{key} must be an object")
    result = {}
    for name, value in raw.items():
        if (
            not isinstance(name, str)
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise SnapshotError(f"invalid {key} entry: {name!r}={value!r}")
        result[name] = value
    return result


def _require_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise SnapshotError(f"{key} must be an array")
    return value


def _ensure_jsonable(value: object) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SnapshotError(f"snapshot is not JSON serializable: {error}") from error
