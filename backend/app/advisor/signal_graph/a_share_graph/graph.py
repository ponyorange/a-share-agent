import math

from .errors import NonMonotonicTickError
from .models import Action, Edge, EdgeKey, LearningConfig, Node, SignalContext


def context_node_ids(context: SignalContext) -> tuple[tuple[str, str], ...]:
    rows = [
        ("market", f"regime:{context.market_regime}"),
        ("industry", f"industry:{context.industry}"),
    ]
    rows.extend(
        ("pattern", f"pattern:{name}") for name in sorted(set(context.patterns))
    )
    rows.append(("stock", f"stock:{context.ticker}"))
    return tuple(rows)


class SignalGraph:
    def __init__(self, config: LearningConfig | None = None):
        self.config = config or LearningConfig()
        self.nodes: dict[str, Node] = {}
        self.edges: dict[EdgeKey, Edge] = {}
        self.outgoing: dict[str, list[EdgeKey]] = {}
        self.aliases: dict[str, str] = {}
        self.axis_ticks: dict[str, int] = {}
        self.context_samples: dict[str, int] = {}

    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.node_id)
        if existing is not None:
            return existing
        self.nodes[node.node_id] = node
        for alias in node.aliases:
            self.aliases[alias] = node.node_id
        return node

    def add_alias(self, canonical_id: str, alias: str) -> None:
        if canonical_id not in self.nodes:
            raise KeyError(canonical_id)
        existing = self.aliases.get(alias)
        if existing is not None and existing != canonical_id:
            raise ValueError(f"alias {alias!r} already maps to {existing!r}")
        if alias != canonical_id:
            self.aliases[alias] = canonical_id
            self.nodes[canonical_id].aliases.add(alias)

    def resolve(self, name: str) -> str | None:
        if name in self.nodes:
            return name
        return self.aliases.get(name)

    def add_edge(self, edge: Edge) -> Edge:
        existing = self.edges.get(edge.key)
        if existing is not None:
            return existing
        if edge.src not in self.nodes or edge.dst not in self.nodes:
            raise ValueError("edge endpoints must exist")
        self.edges[edge.key] = edge
        self.outgoing.setdefault(edge.src, []).append(edge.key)
        return edge

    def get_edge(self, key: EdgeKey) -> Edge:
        return self.edges[key]

    def neighbors(
        self,
        node_id: str,
        *,
        edge_type: str | None = None,
        owner: str | None = None,
        scope_id: str | None = None,
    ) -> tuple[Edge, ...]:
        result = []
        for key in self.outgoing.get(node_id, ()):
            edge = self.edges[key]
            if edge_type is not None and edge.edge_type != edge_type:
                continue
            if owner is not None and edge.owner not in (owner, "default"):
                continue
            if scope_id is not None and edge.scope_id != scope_id:
                continue
            result.append(edge)
        return tuple(result)

    def ensure_context_edges(self, context: SignalContext) -> tuple[EdgeKey, ...]:
        for action in Action:
            self.add_node(
                Node(
                    node_id=f"action:{action.value}",
                    layer="action",
                    node_type="action",
                    label=action.value,
                )
            )

        keys = []
        owners = (
            ("default",)
            if context.owner == "default"
            else ("default", context.owner)
        )
        for layer, node_id in context_node_ids(context):
            self.add_node(
                Node(
                    node_id=node_id,
                    layer=layer,
                    node_type=layer,
                    label=node_id.split(":", 1)[1],
                )
            )
            for owner in owners:
                axis = f"{owner}::{context.scope_id}::{layer}::{node_id}"
                self.set_axis_tick(axis, context.trade_tick)
                for action in Action:
                    edge = self.add_edge(
                        Edge(
                            src=node_id,
                            dst=f"action:{action.value}",
                            edge_type="supports",
                            owner=owner,
                            scope_id=context.scope_id,
                            layer=layer,
                            confidence=self.config.initial_confidence,
                            last_tick=context.trade_tick,
                        )
                    )
                    keys.append(edge.key)
        return tuple(keys)

    def axis_id(self, edge_or_key: Edge | EdgeKey) -> str:
        key = edge_or_key.key if isinstance(edge_or_key, Edge) else edge_or_key
        edge = self.edges[key]
        return f"{key.owner}::{key.scope_id}::{edge.layer}::{key.src}"

    def set_axis_tick(self, axis_id: str, tick: int) -> None:
        current = self.axis_ticks.get(axis_id, 0)
        if tick < current:
            raise NonMonotonicTickError(f"{tick} < {current} for {axis_id}")
        self.axis_ticks[axis_id] = tick

    def confidence_now(self, edge: Edge) -> float:
        axis = self.axis_id(edge)
        now_tick = self.axis_ticks.get(axis, 0)
        if now_tick <= edge.last_tick:
            return edge.confidence
        samples = self.context_samples.get(axis, 0)
        tau = self.config.tau_base + self.config.tau_per_sample * samples
        if tau <= 0:
            raise ValueError("decay tau must be positive")
        return edge.confidence * math.exp(-(now_tick - edge.last_tick) / tau)

    def commit_decision(
        self,
        chosen: EdgeKey,
        *,
        tick: int,
        reward: float,
        penalize: EdgeKey | None = None,
        penalty: float = 0.0,
    ) -> None:
        chosen_edge = self.edges[chosen]
        axis = self.axis_id(chosen)
        if penalize is not None:
            if penalize not in self.edges:
                raise KeyError(penalize)
            if self.axis_id(penalize) != axis:
                raise ValueError("penalized edge must belong to the chosen axis")

        self.set_axis_tick(axis, tick)
        candidates = tuple(
            self.edges[key]
            for key in self.outgoing.get(chosen.src, ())
            if key.edge_type == "supports"
            and key.owner == chosen.owner
            and key.scope_id == chosen.scope_id
        )
        for edge in candidates:
            edge.confidence = self.confidence_now(edge)
            edge.last_tick = tick
            edge.sample_count += 1

        chosen_edge.confidence = self._clamp(chosen_edge.confidence + reward)
        chosen_edge.commits += reward
        if penalize is not None and penalize != chosen:
            penalized = self.edges[penalize]
            penalized.confidence = self._clamp(penalized.confidence - penalty)
            penalized.commits -= penalty
        self.context_samples[axis] = self.context_samples.get(axis, 0) + 1

    def dump_state(self) -> dict[str, object]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "axis_ticks": dict(self.axis_ticks),
            "context_samples": dict(self.context_samples),
        }

    def _clamp(self, value: float) -> float:
        return min(
            self.config.confidence_ceiling,
            max(self.config.confidence_floor, value),
        )
