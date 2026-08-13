"""Persist SignalGraph + PredictionLedger snapshots (Mongo with memory fallback)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from .a_share_graph.feedback import PredictionLedger
from .a_share_graph.graph import SignalGraph
from .a_share_graph.snapshot import dump_snapshot, load_snapshot

_LOCK = threading.RLock()
_MEMORY: dict[str, dict[str, Any]] = {}


def _doc_key(owner: str) -> str:
    return f"signal_graph:{owner}"


def _collection():
    from ...db import get_db

    return get_db().signal_graph_state


def load_runtime(owner: str = "default") -> tuple[SignalGraph, PredictionLedger, dict[str, Any]]:
    """Return graph, ledger, and meta (tick calendar)."""
    with _LOCK:
        cached = _MEMORY.get(owner)
        if cached is not None:
            return cached["graph"], cached["ledger"], dict(cached["meta"])

        payload: dict[str, Any] | None = None
        try:
            doc = _collection().find_one({"_id": _doc_key(owner)}, {"_id": 0})
            if doc and isinstance(doc.get("snapshot"), dict):
                payload = doc
        except Exception:
            payload = None

        if payload is None:
            graph = SignalGraph()
            ledger = PredictionLedger()
            meta = {"owner": owner, "tick_by_date": {}, "date_by_tick": {}}
        else:
            graph, ledger = load_snapshot(payload["snapshot"])
            meta = dict(payload.get("meta") or {})
            meta.setdefault("owner", owner)
            meta.setdefault("tick_by_date", {})
            meta.setdefault("date_by_tick", {})

        _MEMORY[owner] = {"graph": graph, "ledger": ledger, "meta": meta}
        return graph, ledger, dict(meta)


def save_runtime(
    owner: str,
    graph: SignalGraph,
    ledger: PredictionLedger,
    meta: dict[str, Any],
) -> dict[str, Any]:
    snapshot = dump_snapshot(graph, ledger)
    doc = {
        "_id": _doc_key(owner),
        "owner": owner,
        "snapshot": snapshot,
        "meta": meta,
        "updated_at": datetime.now(timezone.utc),
        "pending_count": len(ledger.pending),
        "settled_count": len(ledger.settled),
        "edge_count": len(graph.edges),
        "node_count": len(graph.nodes),
    }
    with _LOCK:
        _MEMORY[owner] = {
            "graph": graph,
            "ledger": ledger,
            "meta": dict(meta),
        }
        try:
            _collection().replace_one({"_id": doc["_id"]}, doc, upsert=True)
        except Exception:
            # Memory remains authoritative for this process if Mongo is down.
            pass
    return {
        "owner": owner,
        "pending_count": doc["pending_count"],
        "settled_count": doc["settled_count"],
        "edge_count": doc["edge_count"],
        "node_count": doc["node_count"],
        "updated_at": doc["updated_at"].isoformat(),
    }


def reset_memory(owner: str | None = None) -> None:
    """Test helper: drop in-process cache."""
    with _LOCK:
        if owner is None:
            _MEMORY.clear()
        else:
            _MEMORY.pop(owner, None)


def summary(owner: str = "default") -> dict[str, Any]:
    graph, ledger, meta = load_runtime(owner)
    return {
        "owner": owner,
        "pending_count": len(ledger.pending),
        "unresolved_count": len(ledger.unresolved),
        "settled_count": len(ledger.settled),
        "edge_count": len(graph.edges),
        "node_count": len(graph.nodes),
        "tick_dates": len(meta.get("tick_by_date") or {}),
        "latest_trade_date": _latest_date(meta),
        "latest_trade_tick": _latest_tick(meta),
        "last_evolve_date": meta.get("last_evolve_date"),
        "last_evolve_at": meta.get("last_evolve_at"),
    }


def _latest_date(meta: dict[str, Any]) -> str | None:
    dates = list((meta.get("tick_by_date") or {}).keys())
    return max(dates) if dates else None


def _latest_tick(meta: dict[str, Any]) -> int | None:
    ticks = [int(v) for v in (meta.get("tick_by_date") or {}).values()]
    return max(ticks) if ticks else None
