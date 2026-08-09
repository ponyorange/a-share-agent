"""Unit tests for SignalGraph adapter helpers (no live market I/O)."""

from __future__ import annotations

from app.advisor.signal_graph.a_share_graph.feedback import FeedbackEngine, PredictionLedger
from app.advisor.signal_graph.a_share_graph.graph import SignalGraph
from app.advisor.signal_graph.a_share_graph.models import MarketState, SignalContext
from app.advisor.signal_graph.a_share_graph.signals import SignalEngine
from app.advisor.signal_graph.context_builder import infer_patterns, map_regime_to_graph
from app.advisor.signal_graph.serialize import (
    decision_to_dict,
    map_graph_action_to_product,
)
from app.advisor.signal_graph.service import (
    resolve_trade_tick,
    run_synthetic_demo,
)
from app.advisor.signal_graph import store as graph_store


def test_map_regime_to_graph():
    assert map_regime_to_graph({"trend_regime": "downtrend"}) == "bear"
    assert map_regime_to_graph({"gate_level": "risk_off"}) == "bear"
    assert (
        map_regime_to_graph(
            {"trend_regime": "uptrend", "sentiment_cycle": "strengthen"}
        )
        == "bull"
    )
    assert map_regime_to_graph({"trend_regime": "range"}) == "sideways"


def test_infer_patterns_from_factors():
    patterns = infer_patterns({"mom_20": 0.05, "vol_ratio": 2.0, "ma20_bias": 0.03})
    assert "momentum_up" in patterns
    assert "volume_breakout" in patterns
    assert "ma_bullish" in patterns


def test_map_graph_action_to_product():
    assert map_graph_action_to_product("BUY", has_position=False) == "buy"
    assert map_graph_action_to_product("BUY", has_position=True) == "add"
    assert map_graph_action_to_product("SELL", has_position=True) == "sell"
    assert map_graph_action_to_product("HOLD", has_position=False) == "watch"


def test_resolve_trade_tick_is_idempotent():
    meta: dict = {"tick_by_date": {}, "date_by_tick": {}}
    t1, meta = resolve_trade_tick(meta, "2026-08-07")
    t2, meta = resolve_trade_tick(meta, "2026-08-07")
    t3, meta = resolve_trade_tick(meta, "2026-08-08")
    assert t1 == t2 == 0
    assert t3 == 1
    assert meta["date_by_tick"]["0"] == "2026-08-07"


def test_kernel_generate_and_settle_roundtrip():
    graph_store.reset_memory()
    graph = SignalGraph()
    ledger = PredictionLedger()
    engine = SignalEngine(graph, ledger)
    decision = engine.generate(
        SignalContext(
            ticker="600519.SH",
            trade_date="2026-08-01",
            trade_tick=0,
            market_regime="sideways",
            industry="food",
            patterns=("neutral",),
            horizon_days=5,
        ),
        MarketState(ticker="600519.SH"),
        entry_price=100.0,
        benchmark_entry_price=4000.0,
    )
    payload = decision_to_dict(decision)
    assert payload["action"] in {"BUY", "HOLD", "SELL"}
    assert "scores" in payload

    # Cold start should HOLD without prediction when scores weak; seed edge then settle.
    if decision.prediction_id is None:
        # Force a registered prediction on a later tick with enough samples via settle path
        # by manually registering through a second generate after fake sample boost:
        for key in graph.ensure_context_edges(
            SignalContext(
                ticker="600519.SH",
                trade_date="2026-08-01",
                trade_tick=0,
                market_regime="sideways",
                industry="food",
                patterns=("neutral",),
                horizon_days=5,
            )
        ):
            edge = graph.get_edge(key)
            edge.sample_count = 10
            edge.confidence = 2.0 if key.dst.endswith("BUY") else 0.1
        decision = engine.generate(
            SignalContext(
                ticker="600519.SH",
                trade_date="2026-08-02",
                trade_tick=1,
                market_regime="sideways",
                industry="food",
                patterns=("neutral",),
                horizon_days=5,
            ),
            MarketState(ticker="600519.SH"),
            entry_price=100.0,
            benchmark_entry_price=4000.0,
        )

    assert decision.prediction_id
    result = FeedbackEngine(graph, ledger).settle(
        decision.prediction_id,
        current_tick=decision.prediction_id and 6 or 6,
        stock_exit=105.0,
        benchmark_exit=4010.0,
    )
    assert result.status == "settled"
    assert result.excess_return is not None


def test_synthetic_demo_runs():
    out = run_synthetic_demo(seed=3, days=30)
    assert out["signal_count"] > 0
    assert out["settled_count"] > 0
    assert out["edge_count"] > 0
