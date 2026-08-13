from app.advisor.signal_graph.a_share_graph.feedback import PredictionLedger
from app.advisor.signal_graph.a_share_graph.graph import SignalGraph
from app.advisor.signal_graph.a_share_graph.models import Edge, Node
from app.advisor.signal_graph.service import view_graph


def _seed(graph: SignalGraph) -> None:
    for nid, layer, label in (
        ("industry:food", "industry", "food"),
        ("stock:600519.SH", "stock", "600519.SH"),
        ("action:BUY", "action", "BUY"),
        ("action:HOLD", "action", "HOLD"),
        ("action:SELL", "action", "SELL"),
    ):
        graph.add_node(Node(node_id=nid, layer=layer, node_type=layer, label=label))
    graph.add_edge(
        Edge(
            src="industry:food",
            dst="action:BUY",
            edge_type="supports",
            owner="default",
            scope_id="5::bull",
            layer="industry",
            confidence=1.0,
            sample_count=2,
            last_tick=3,
        )
    )
    graph.add_edge(
        Edge(
            src="industry:food",
            dst="action:BUY",
            edge_type="supports",
            owner="default",
            scope_id="5::bear",
            layer="industry",
            confidence=3.0,
            sample_count=2,
            last_tick=8,
        )
    )
    graph.add_edge(
        Edge(
            src="stock:600519.SH",
            dst="action:HOLD",
            edge_type="supports",
            owner="default",
            scope_id="5::bull",
            layer="stock",
            confidence=0.2,
            sample_count=0,
            last_tick=1,
        )
    )


def _runtime(graph: SignalGraph):
    ledger = PredictionLedger()
    meta = {"owner": "default", "tick_by_date": {}, "date_by_tick": {}}
    return graph, ledger, meta


def test_view_graph_merges_same_src_dst(monkeypatch):
    graph = SignalGraph()
    _seed(graph)
    monkeypatch.setattr(
        "app.advisor.signal_graph.service.graph_store.load_runtime",
        lambda owner="default": _runtime(graph),
    )

    out = view_graph(owner="default")
    assert set(out) == {"truncated", "node_count", "edge_count", "nodes", "edges"}
    keys = {(e["src"], e["dst"]) for e in out["edges"]}
    assert ("industry:food", "action:BUY") in keys
    merged = next(e for e in out["edges"] if e["src"] == "industry:food")
    assert merged["sample_count"] == 4
    assert merged["confidence"] == 2.0
    assert merged["last_tick"] == 8
    assert out["truncated"] is False
    assert "pending" not in out and "snapshot" not in out
    node = out["nodes"][0]
    assert set(node) <= {"id", "layer", "label"}
    edge = out["edges"][0]
    assert set(edge) <= {
        "src",
        "dst",
        "layer",
        "confidence",
        "sample_count",
        "last_tick",
    }


def test_view_graph_truncates_by_strength(monkeypatch):
    graph = SignalGraph()
    for i in range(6):
        sid = f"stock:{i:06d}.SH"
        graph.add_node(Node(node_id=sid, layer="stock", node_type="stock", label=str(i)))
        graph.add_node(
            Node(node_id="action:BUY", layer="action", node_type="action", label="BUY")
        )
        graph.add_edge(
            Edge(
                src=sid,
                dst="action:BUY",
                edge_type="supports",
                owner="default",
                scope_id="5::bull",
                layer="stock",
                confidence=float(i),
                sample_count=i,
                last_tick=i,
            )
        )
    monkeypatch.setattr(
        "app.advisor.signal_graph.service.graph_store.load_runtime",
        lambda owner="default": _runtime(graph),
    )
    out = view_graph(owner="default", max_nodes=10, max_edges=2)
    assert out["truncated"] is True
    assert out["edge_count"] == 2
    srcs = {e["src"] for e in out["edges"]}
    assert "stock:000005.SH" in srcs


def test_view_route_exists():
    from app.advisor.signal_graph import routes as sg_routes

    paths = {getattr(r, "path", None) for r in sg_routes.router.routes}
    assert "/signal-graph/view" in paths
