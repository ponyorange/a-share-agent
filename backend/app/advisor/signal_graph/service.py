"""Shared SignalGraph service for research, recommendations, and agents."""

from __future__ import annotations

import threading
from typing import Any

from ..calendar_util import last_trading_day
from ..config_loader import load_config
from .a_share_graph.feedback import FeedbackEngine
from .a_share_graph.market import normalize_ticker
from .a_share_graph.signals import SignalEngine
from .context_builder import build_signal_inputs, load_exit_prices
from .serialize import decision_to_dict, map_graph_action_to_product
from . import store as graph_store

_WRITE_LOCK = threading.RLock()


def signal_graph_config() -> dict[str, Any]:
    cfg = load_config().get("signal_graph") or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "attach_to_recommendations": bool(cfg.get("attach_to_recommendations", True)),
        "attach_to_advice": bool(cfg.get("attach_to_advice", True)),
        "promote_evidence": bool(cfg.get("promote_evidence", True)),
        "horizon_days": int(cfg.get("horizon_days") or 5),
        "owner": str(cfg.get("owner") or "default"),
        "weight": float(cfg.get("weight") or 0.0),
        "auto_evolve": bool(cfg.get("auto_evolve", True)),
        "evolve_generate": bool(cfg.get("evolve_generate", True)),
        "evolve_settle_limit": int(cfg.get("evolve_settle_limit") or 200),
        "evolve_generate_limit": int(cfg.get("evolve_generate_limit") or 40),
    }


def resolve_trade_tick(meta: dict[str, Any], trade_date: str) -> tuple[int, dict[str, Any]]:
    """Assign a monotonic tick for trade_date (idempotent)."""
    day = trade_date[:10]
    tick_by_date = dict(meta.get("tick_by_date") or {})
    date_by_tick = dict(meta.get("date_by_tick") or {})
    if day in tick_by_date:
        return int(tick_by_date[day]), meta

    next_tick = 0
    if tick_by_date:
        next_tick = max(int(v) for v in tick_by_date.values()) + 1
    # Keep ticks non-decreasing even if dates arrive out of order: use max+1.
    tick_by_date[day] = next_tick
    date_by_tick[str(next_tick)] = day
    meta = {**meta, "tick_by_date": tick_by_date, "date_by_tick": date_by_tick}
    return next_tick, meta


def date_for_tick(meta: dict[str, Any], tick: int) -> str | None:
    return (meta.get("date_by_tick") or {}).get(str(int(tick)))


def get_summary(owner: str | None = None) -> dict[str, Any]:
    cfg = signal_graph_config()
    oid = owner or cfg["owner"]
    return {**graph_store.summary(oid), "config": cfg}


def generate_signal(
    symbol: str,
    *,
    trade_date: str | None = None,
    persist: bool = True,
    register_prediction: bool = True,
) -> dict[str, Any]:
    """Generate a BUY/HOLD/SELL decision for one symbol."""
    cfg = signal_graph_config()
    if not cfg["enabled"]:
        raise RuntimeError("signal_graph 未启用")

    owner = cfg["owner"]
    day = (trade_date or last_trading_day())[:10]

    with _WRITE_LOCK:
        graph, ledger, meta = graph_store.load_runtime(owner)
        tick, meta = resolve_trade_tick(meta, day)
        inputs = build_signal_inputs(
            symbol,
            trade_date=day,
            trade_tick=tick,
            horizon_days=cfg["horizon_days"],
            owner=owner,
        )
        engine = SignalEngine(graph, ledger)
        if not register_prediction:
            # Peek without registering: use a temporary ledger clone path —
            # SignalEngine always registers; for peek we still register then
            # callers who only want scores should use persist path.
            pass
        decision = engine.generate(
            inputs["context"],
            inputs["market_state"],
            entry_price=float(inputs["entry_price"]),
            benchmark_entry_price=float(inputs["benchmark_entry_price"]),
        )
        if persist:
            graph_store.save_runtime(owner, graph, ledger, meta)

    payload = {
        "symbol": inputs["symbol"],
        "name": inputs["name"],
        "ticker": inputs["context"].ticker,
        "trade_date": day,
        "trade_tick": tick,
        "horizon_days": cfg["horizon_days"],
        "market_regime": inputs["market_regime"],
        "industry": inputs["industry"],
        "patterns": inputs["patterns"],
        "close": inputs["close"],
        "prev_close": inputs["prev_close"],
        "day_chg_pct": inputs["day_chg_pct"],
        "entry_price": inputs["entry_price"],
        "benchmark_entry_price": inputs["benchmark_entry_price"],
        **decision_to_dict(decision),
        "product_action": map_graph_action_to_product(
            decision.action.value, has_position=False
        ),
    }
    return payload


def generate_signals_batch(
    symbols: list[str],
    *,
    trade_date: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for raw in symbols:
        sym = str(raw or "").strip()
        if not sym:
            continue
        try:
            items.append(
                generate_signal(sym, trade_date=day, persist=persist)
            )
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})
    return {
        "trade_date": day,
        "count": len(items),
        "items": items,
        "errors": errors,
        "summary": get_summary(),
    }


def settle_due(
    *,
    trade_date: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Settle pending predictions whose due_tick is reachable by trade_date."""
    cfg = signal_graph_config()
    owner = cfg["owner"]
    day = (trade_date or last_trading_day())[:10]
    settled_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    with _WRITE_LOCK:
        graph, ledger, meta = graph_store.load_runtime(owner)
        current_tick, meta = resolve_trade_tick(meta, day)
        engine = FeedbackEngine(graph, ledger)
        due = [
            p
            for p in ledger.pending.values()
            if int(p.due_tick) <= int(current_tick)
        ]
        due.sort(key=lambda p: (p.due_tick, p.prediction_id))
        for prediction in due[: max(0, limit)]:
            exit_date = date_for_tick(meta, int(prediction.due_tick)) or day
            symbol = prediction.context.ticker.split(".", 1)[0]
            try:
                stock_exit, bench_exit = load_exit_prices(
                    symbol, trade_date=exit_date
                )
                result = engine.settle(
                    prediction.prediction_id,
                    current_tick=current_tick,
                    stock_exit=stock_exit,
                    benchmark_exit=bench_exit,
                )
                settled_rows.append(
                    {
                        "prediction_id": result.prediction_id,
                        "status": result.status,
                        "excess_return": result.excess_return,
                        "feedback_delta": result.feedback_delta,
                        "ticker": prediction.context.ticker,
                        "action": prediction.action.value,
                        "due_tick": prediction.due_tick,
                        "exit_date": exit_date,
                    }
                )
            except Exception as exc:
                try:
                    engine.mark_unresolved(prediction.prediction_id)
                    unresolved_rows.append(
                        {
                            "prediction_id": prediction.prediction_id,
                            "ticker": prediction.context.ticker,
                            "error": str(exc),
                        }
                    )
                except Exception as inner:
                    skipped.append(
                        {
                            "prediction_id": prediction.prediction_id,
                            "error": f"{exc}; mark_unresolved failed: {inner}",
                        }
                    )
        graph_store.save_runtime(owner, graph, ledger, meta)

    return {
        "trade_date": day,
        "current_tick": current_tick,
        "settled": settled_rows,
        "unresolved": unresolved_rows,
        "skipped": skipped,
        "summary": get_summary(owner),
    }


def attach_graph_fields(
    row: dict[str, Any],
    *,
    has_position: bool | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Best-effort attach graph_signal onto an advice/recommendation row."""
    cfg = signal_graph_config()
    if not cfg["enabled"]:
        return row
    symbol = str(row.get("symbol") or "").strip()
    if not symbol or row.get("error"):
        return row
    as_of = str(row.get("as_of") or "")[:10] or None
    try:
        signal = generate_signal(symbol, trade_date=as_of, persist=persist)
    except Exception as exc:
        row["graph_signal"] = {"error": str(exc)}
        return row

    held = (
        bool(has_position)
        if has_position is not None
        else bool(row.get("has_position"))
    )
    row["graph_signal"] = {
        "action": signal.get("action"),
        "raw_action": signal.get("raw_action"),
        "scores": signal.get("scores"),
        "margin": signal.get("margin"),
        "prediction_id": signal.get("prediction_id"),
        "blocked_reason": signal.get("blocked_reason"),
        "evidence": signal.get("evidence"),
        "market_regime": signal.get("market_regime"),
        "patterns": signal.get("patterns"),
        "trade_tick": signal.get("trade_tick"),
        "horizon_days": signal.get("horizon_days"),
        "product_action": map_graph_action_to_product(
            str(signal.get("action") or "HOLD"), has_position=held
        ),
    }
    return row


def get_signal_view(symbol: str, *, trade_date: str | None = None) -> dict[str, Any]:
    """Public read used by Agent / promote — generates (and persists) one signal."""
    return generate_signal(symbol, trade_date=trade_date, persist=True)


def list_pending(limit: int = 50) -> dict[str, Any]:
    cfg = signal_graph_config()
    _graph, ledger, meta = graph_store.load_runtime(cfg["owner"])
    rows = []
    for prediction in sorted(
        ledger.pending.values(),
        key=lambda p: (p.due_tick, p.prediction_id),
    )[: max(0, limit)]:
        rows.append(
            {
                "prediction_id": prediction.prediction_id,
                "ticker": prediction.context.ticker,
                "trade_date": prediction.context.trade_date,
                "action": prediction.action.value,
                "due_tick": prediction.due_tick,
                "due_date": date_for_tick(meta, int(prediction.due_tick)),
                "entry_price": prediction.entry_price,
                "benchmark_entry_price": prediction.benchmark_entry_price,
                "status": prediction.status,
            }
        )
    return {"count": len(rows), "items": rows, "summary": get_summary()}


def list_settled(limit: int = 50) -> dict[str, Any]:
    cfg = signal_graph_config()
    _graph, ledger, _meta = graph_store.load_runtime(cfg["owner"])
    rows = []
    for prediction_id, (prediction, result) in sorted(
        ledger.settled.items(),
        key=lambda kv: kv[0],
        reverse=True,
    )[: max(0, limit)]:
        rows.append(
            {
                "prediction_id": prediction_id,
                "ticker": prediction.context.ticker,
                "trade_date": prediction.context.trade_date,
                "action": prediction.action.value,
                "status": result.status,
                "excess_return": result.excess_return,
                "feedback_delta": result.feedback_delta,
            }
        )
    return {"count": len(rows), "items": rows, "summary": get_summary()}


def run_synthetic_demo(seed: int = 7, days: int = 60) -> dict[str, Any]:
    """In-process synthetic backtest (does not touch the persisted graph)."""
    from .a_share_graph.feedback import FeedbackEngine, PredictionLedger
    from .a_share_graph.graph import SignalGraph
    from .a_share_graph.models import MarketState, SignalContext
    from .a_share_graph.signals import SignalEngine
    import random
    from datetime import date, timedelta

    if days < 15:
        raise ValueError("days must be at least 15")

    stocks = {
        "600001.SH": "bank",
        "000001.SZ": "technology",
    }
    regimes = []
    for i in range(days):
        if i < days // 3:
            regimes.append("bull")
        elif i < 2 * days // 3:
            regimes.append("sideways")
        else:
            regimes.append("bear")

    rng = random.Random(seed)
    bench = [100.0]
    prices = {t: [100.0] for t in stocks}
    for i in range(1, days):
        drift = {"bull": 0.001, "sideways": 0.0, "bear": -0.001}[regimes[i]]
        bench.append(bench[-1] * (1 + drift + rng.uniform(-0.01, 0.01)))
        for t in stocks:
            prices[t].append(
                prices[t][-1] * (1 + drift + rng.uniform(-0.015, 0.015))
            )

    graph = SignalGraph()
    ledger = PredictionLedger()
    signals = SignalEngine(graph, ledger)
    feedback = FeedbackEngine(graph, ledger)
    signal_count = 0
    settled_count = 0
    correct = 0
    start = date(2026, 1, 1)

    for tick in range(days):
        trade_date = (start + timedelta(days=tick)).isoformat()
        for ticker, industry in stocks.items():
            decision = signals.generate(
                SignalContext(
                    ticker=ticker,
                    trade_date=trade_date,
                    trade_tick=tick,
                    market_regime=regimes[tick],
                    industry=industry,
                    patterns=("momentum_up",) if regimes[tick] == "bull" else ("neutral",),
                    horizon_days=5,
                ),
                MarketState(ticker),
                entry_price=prices[ticker][tick],
                benchmark_entry_price=bench[tick],
            )
            if decision.prediction_id:
                signal_count += 1
        for prediction in [
            p for p in list(ledger.pending.values()) if p.due_tick == tick
        ]:
            result = feedback.settle(
                prediction.prediction_id,
                current_tick=tick,
                stock_exit=prices[prediction.context.ticker][tick],
                benchmark_exit=bench[tick],
            )
            settled_count += 1
            if result.feedback_delta > 0:
                correct += 1

    return {
        "seed": seed,
        "days": days,
        "signal_count": signal_count,
        "settled_count": settled_count,
        "positive_feedback": correct,
        "edge_count": len(graph.edges),
        "node_count": len(graph.nodes),
        "note": "合成行情仅验证学习闭环，不代表真实收益",
    }


def normalize_symbol_to_ticker(symbol: str) -> str:
    return normalize_ticker(symbol)


DEFAULT_VIEW_MAX_NODES = 8000
DEFAULT_VIEW_MAX_EDGES = 20000


def edge_strength(sample_count: int, confidence: float) -> float:
    return float(sample_count) * (0.5 + abs(float(confidence)))


def view_graph(
    *,
    owner: str | None = None,
    max_nodes: int | None = None,
    max_edges: int | None = None,
) -> dict[str, Any]:
    cfg = signal_graph_config()
    oid = owner or str(cfg.get("owner") or "default")
    cap_n = int(max_nodes or DEFAULT_VIEW_MAX_NODES)
    cap_e = int(max_edges or DEFAULT_VIEW_MAX_EDGES)
    graph, _ledger, _meta = graph_store.load_runtime(oid)

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in graph.edges.values():
        dst = str(edge.dst)
        if not dst.startswith("action:"):
            continue
        key = (str(edge.src), dst)
        sample = int(edge.sample_count or 0)
        conf = float(edge.confidence or 0.0)
        cur = merged.get(key)
        if cur is None:
            merged[key] = {
                "src": key[0],
                "dst": key[1],
                "layer": edge.layer,
                "confidence": conf,
                "sample_count": sample,
                "last_tick": int(edge.last_tick or 0),
                "_wconf": conf * sample,
            }
            continue
        cur["sample_count"] += sample
        cur["_wconf"] += conf * sample
        cur["last_tick"] = max(int(cur["last_tick"]), int(edge.last_tick or 0))
        if cur["sample_count"] > 0:
            cur["confidence"] = cur["_wconf"] / cur["sample_count"]
        else:
            cur["confidence"] = (float(cur["confidence"]) + conf) / 2.0

    rows = []
    for item in merged.values():
        item.pop("_wconf", None)
        rows.append(item)
    rows.sort(
        key=lambda r: (
            -edge_strength(int(r["sample_count"]), float(r["confidence"])),
            r["src"],
            r["dst"],
        )
    )

    truncated = False
    chosen: list[dict[str, Any]] = []
    nodes_acc: dict[str, dict[str, Any]] = {}

    def _add_node(nid: str) -> bool:
        if nid in nodes_acc:
            return True
        if len(nodes_acc) >= cap_n:
            return False
        raw = graph.nodes.get(nid)
        nodes_acc[nid] = {
            "id": nid,
            "layer": raw.layer if raw is not None else (
                "action" if nid.startswith("action:") else "stock"
            ),
            "label": (raw.label if raw is not None else nid.split(":", 1)[-1])
            or nid,
        }
        return True

    for row in rows:
        if len(chosen) >= cap_e:
            truncated = True
            break
        if row["src"] not in nodes_acc and len(nodes_acc) >= cap_n:
            truncated = True
            continue
        if row["dst"] not in nodes_acc and len(nodes_acc) >= cap_n:
            truncated = True
            continue
        if not _add_node(row["src"]) or not _add_node(row["dst"]):
            truncated = True
            continue
        chosen.append(row)

    if len(rows) > len(chosen):
        truncated = True

    return {
        "truncated": truncated,
        "node_count": len(nodes_acc),
        "edge_count": len(chosen),
        "nodes": sorted(nodes_acc.values(), key=lambda n: n["id"]),
        "edges": chosen,
    }
