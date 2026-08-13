# Signal Graph Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在图学习页用 sigma.js WebGL 画出完整 SignalGraph（瘦节点/边），点选或生成信号时在全图上高亮局部。

**Architecture:** 后端 `view_graph()` 从内存图合并同 src-dst 边并做安全顶截断，经 `GET /signal-graph/view` 返回瘦 JSON。前端纯函数分层赋坐标与高亮集合，再用 graphology + sigma v3 渲染；生成信号只用已有 `evidence` 高亮，不重拉全图。

**Tech Stack:** FastAPI、pytest、TypeScript、React 19、Vitest、`sigma` v3、`graphology`

## Global Constraints

- Spec：`docs/superpowers/specs/2026-08-13-signal-graph-visualization-design.md`
- 节点字段仅 `id, layer, label`；边字段仅 `src, dst, layer, confidence, sample_count, last_tick`
- 禁止把 `dump_snapshot` / pending / settled / unresolved 放进 `/view` payload
- 安全顶：节点 8000、边 20000；当前规模必须全量且 `truncated: false`
- 禁止 ForceAtlas / 每帧重布局 / `@react-sigma/core` / `graphology-layout-forceatlas2`
- 标签 LOD：远景只显示 `action:*` 与当前高亮/hover 节点
- 路由卸载必须 `sigma.kill()`
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交
- 镜像标签仍为 `名称:架构`（如 `share-data:amd64`），禁止部署默认用 `latest`

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/signal_graph/service.py` | `view_graph`：合并、截断、瘦序列化 |
| `backend/app/advisor/signal_graph/__init__.py` | 导出 `view_graph` |
| `backend/app/advisor/signal_graph/routes.py` | `GET /view` |
| `backend/tests/test_signal_graph_view.py` | 合并 / 安全顶 / 瘦字段 |
| `frontend-advisor/src/api.ts` | `GraphViewPayload` + `fetchSignalGraphView` |
| `frontend-advisor/src/signalGraphLayout.ts` | 分层坐标、高亮、LOD（无 WebGL） |
| `frontend-advisor/src/signalGraphLayout.test.ts` | 纯函数单测 |
| `frontend-advisor/src/components/SignalGraphView.tsx` | 挂载 sigma、交互、卸载 kill |
| `frontend-advisor/src/pages/SignalGraphPage.tsx` | 并行拉 `/view`；生成后 evidence 高亮 |
| `frontend-advisor/src/styles.css` | 画布高度 520px |
| `frontend-advisor/package.json` | 增加 `sigma`、`graphology` |

---

### Task 1: 后端 `view_graph` 瘦全图

**Files:**
- Create: `backend/tests/test_signal_graph_view.py`
- Modify: `backend/app/advisor/signal_graph/service.py`（文件末尾追加 `view_graph` 及辅助函数）
- Modify: `backend/app/advisor/signal_graph/__init__.py`

**Interfaces:**
- Consumes: `store.load_runtime(owner)` → `(SignalGraph, ledger, meta)`；只用 `graph.nodes` / `graph.edges`，忽略 ledger
- Produces:
  - `DEFAULT_VIEW_MAX_NODES = 8000`
  - `DEFAULT_VIEW_MAX_EDGES = 20000`
  - `edge_strength(sample_count: int, confidence: float) -> float`  
    `sample_count * (0.5 + abs(confidence))`
  - `view_graph(*, owner: str | None = None, max_nodes: int | None = None, max_edges: int | None = None) -> dict[str, Any]`  
    返回键仅：`truncated, node_count, edge_count, nodes, edges`  
    `nodes[]` 每项 `id, layer, label`  
    `edges[]` 每项 `src, dst, layer, confidence, sample_count, last_tick`  
    同 `(src, dst)` 合并：`sample_count` 相加；`confidence` 按样本加权平均（两边 sample 都为 0 则取算术平均）；`last_tick` 取 max；`layer` 取第一条  
    合并后若边数 > `max_edges` 或端点数 > `max_nodes`：按 `edge_strength` 降序纳入边，直到任一顶；`truncated=True`  
    `owner` 默认 `signal_graph_config()["owner"]`  
    图未启用或空：仍 200 语义（函数返回空数组，`truncated=False`）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_signal_graph_view.py
from app.advisor.signal_graph.a_share_graph.graph import SignalGraph
from app.advisor.signal_graph.a_share_graph.models import Edge, Node
from app.advisor.signal_graph import store as graph_store
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


def test_view_graph_merges_same_src_dst(monkeypatch):
    graph_store.reset_memory()
    graph, ledger, meta = graph_store.load_runtime("default")
    _seed(graph)
    graph_store.save_runtime("default", graph, ledger, meta)

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
    graph_store.reset_memory()
    graph, ledger, meta = graph_store.load_runtime("default")
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
    graph_store.save_runtime("default", graph, ledger, meta)
    out = view_graph(owner="default", max_nodes=10, max_edges=2)
    assert out["truncated"] is True
    assert out["edge_count"] == 2
    srcs = {e["src"] for e in out["edges"]}
    assert "stock:000005.SH" in srcs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_signal_graph_view.py -v`  
Expected: FAIL `view_graph` 未定义

- [ ] **Step 3: Write minimal implementation**

在 `service.py` 追加（保留现有函数不动）：

```python
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
```

`__init__.py` 增加 `view_graph` 导入与 `__all__`。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_signal_graph_view.py tests/test_signal_graph_service.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**（默认跳过）

```bash
git add backend/app/advisor/signal_graph/service.py backend/app/advisor/signal_graph/__init__.py backend/tests/test_signal_graph_view.py
git commit -m "feat: expose a slim full-graph view payload"
```

---

### Task 2: `GET /signal-graph/view` + 前端 API 类型

**Files:**
- Modify: `backend/app/advisor/signal_graph/routes.py`
- Modify: `frontend-advisor/src/api.ts`（`GraphSignalEvidence` 附近追加类型与 `fetchSignalGraphView`）
- Create: `backend/tests/test_signal_graph_view_route.py`（若项目已有 TestClient 惯例则跟随；否则用直接调 route 函数 + monkeypatch）

**Interfaces:**
- Consumes: `sg.view_graph`
- Produces: `GET /api/advisor/signal-graph/view`  
  Query 可选 `max_nodes: int | None`（1–8000）、`max_edges: int | None`（1–20000）；前端生产调用不传  
  `fetchSignalGraphView(): Promise<GraphViewPayload>`

先看 `backend/tests/` 里是否已有 `TestClient` 用法。若有，对 `/api/advisor/signal-graph/view` 做 200 断言 payload 键。若鉴权难测，改为：

```python
def test_view_route_delegates(monkeypatch):
    from app.advisor.signal_graph import routes as rt
    monkeypatch.setattr(rt.sg, "view_graph", lambda **kw: {"truncated": False, "node_count": 0, "edge_count": 0, "nodes": [], "edges": []})
    # 直接调用底层 sg.view_graph 已在 Task 1 覆盖；本任务至少把路由函数接上
```

本任务最小：路由存在且调用 `view_graph`。用 FastAPI `TestClient` 时需带现有测试用户 fixture；搜 `backend/tests/test_` 里 `signal-graph` 或 `get_current_user` override。没有现成 fixture 就只测 `sg.view_graph` 已被 route 引用（import routes 不报错）+ 前端类型编译。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_signal_graph_view.py 追加
from app.advisor.signal_graph import routes as sg_routes

def test_view_route_exists():
    paths = {getattr(r, "path", None) for r in sg_routes.router.routes}
    assert "/view" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_signal_graph_view.py::test_view_route_exists -v`  
Expected: FAIL `'/view' not in paths`

- [ ] **Step 3: Write minimal implementation**

`routes.py` 在 `pending` 之前追加：

```python
@router.get("/view")
def signal_graph_view(
    max_nodes: int | None = Query(default=None, ge=1, le=8000),
    max_edges: int | None = Query(default=None, ge=1, le=20000),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    return sg.view_graph(max_nodes=max_nodes, max_edges=max_edges)
```

`api.ts`：

```typescript
export type GraphViewNode = {
  id: string
  layer: string
  label: string
}

export type GraphViewEdge = {
  src: string
  dst: string
  layer: string
  confidence: number
  sample_count: number
  last_tick: number
}

export type GraphViewPayload = {
  truncated: boolean
  node_count: number
  edge_count: number
  nodes: GraphViewNode[]
  edges: GraphViewEdge[]
}

export function fetchSignalGraphView(): Promise<GraphViewPayload> {
  return authFetch('/api/advisor/signal-graph/view')
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_signal_graph_view.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**（默认跳过）

```bash
git add backend/app/advisor/signal_graph/routes.py backend/tests/test_signal_graph_view.py frontend-advisor/src/api.ts
git commit -m "feat: add signal-graph view HTTP endpoint"
```

---

### Task 3: 分层坐标、高亮、LOD 纯函数

**Files:**
- Create: `frontend-advisor/src/signalGraphLayout.ts`
- Create: `frontend-advisor/src/signalGraphLayout.test.ts`

**Interfaces:**
- Consumes: `GraphViewNode`, `GraphViewEdge`, `GraphSignalEvidence`（从 `./api` 导入）
- Produces:
  - `LAYER_X: Record<string, number>` = `{ market: 0, industry: 1, pattern: 2, stock: 3, action: 4 }`
  - `ACTION_ORDER = ['BUY', 'HOLD', 'SELL']`
  - `edgeKey(src: string, dst: string): string` → `` `${src}->${dst}` ``
  - `layerCoordinates(nodes: GraphViewNode[]): Record<string, { x: number; y: number }>`  
    按 `layer` 分组；组内 `label` localeCompare；`y` 均匀落在 `[0, 1]`（仅 1 个则 y=0.5）；`action` 层按 BUY/HOLD/SELL 固定顺序（缺的跳过）  
    未知 layer 的 x=2
  - `HighlightState = { nodeIds: Set<string>; edgeKeys: Set<string> }`
  - `highlightFromNode(nodeId: string, edges: GraphViewEdge[]): HighlightState`  
    含 `nodeId` 与所有 `src===nodeId && dst.startsWith('action:')` 的边，以及这些边的 `dst`
  - `highlightFromEvidence(evidence: GraphSignalEvidence[], action?: string): HighlightState`  
    每个 evidence 的 `src`、`dst`（若无 dst 且有 action 则 `action:${action}`）、`edgeKey(src,dst)`；再加 `action:${action}`（若传入）
  - `shouldShowLabel(args: { id: string; layer: string; ratio: number; hoveredId?: string | null; highlighted: Set<string> }): boolean`  
    `layer==='action'` → true；`id===hoveredId` → true；`highlighted.has(id)` → true；`ratio < 0.35` → true；否则 false  
    （sigma camera `ratio` 越小越近；0.35 为放大阈值，单测锁定此数）

- [ ] **Step 1: Write the failing test**

```typescript
// frontend-advisor/src/signalGraphLayout.test.ts
import { describe, expect, it } from 'vitest'
import {
  LAYER_X,
  edgeKey,
  highlightFromEvidence,
  highlightFromNode,
  layerCoordinates,
  shouldShowLabel,
} from './signalGraphLayout'

it('assigns x by layer and stacks y', () => {
  const pos = layerCoordinates([
    { id: 'industry:b', layer: 'industry', label: 'b' },
    { id: 'industry:a', layer: 'industry', label: 'a' },
    { id: 'action:BUY', layer: 'action', label: 'BUY' },
    { id: 'action:SELL', layer: 'action', label: 'SELL' },
    { id: 'action:HOLD', layer: 'action', label: 'HOLD' },
  ])
  expect(pos['industry:a'].x).toBe(LAYER_X.industry)
  expect(pos['industry:a'].y).toBeLessThan(pos['industry:b'].y)
  expect(pos['action:BUY'].y).toBeLessThan(pos['action:HOLD'].y)
  expect(pos['action:HOLD'].y).toBeLessThan(pos['action:SELL'].y)
})

it('highlights a context node neighborhood', () => {
  const h = highlightFromNode('industry:food', [
    { src: 'industry:food', dst: 'action:BUY', layer: 'industry', confidence: 1, sample_count: 2, last_tick: 1 },
    { src: 'stock:x', dst: 'action:SELL', layer: 'stock', confidence: 1, sample_count: 2, last_tick: 1 },
  ])
  expect(h.nodeIds.has('industry:food')).toBe(true)
  expect(h.nodeIds.has('action:BUY')).toBe(true)
  expect(h.edgeKeys.has(edgeKey('industry:food', 'action:BUY'))).toBe(true)
  expect(h.edgeKeys.has(edgeKey('stock:x', 'action:SELL'))).toBe(false)
})

it('builds highlight from evidence without refetch', () => {
  const h = highlightFromEvidence(
    [{ src: 'pattern:momentum_up', dst: 'action:BUY', action: 'BUY' }],
    'BUY',
  )
  expect(h.nodeIds.has('pattern:momentum_up')).toBe(true)
  expect(h.nodeIds.has('action:BUY')).toBe(true)
})

it('hides stock labels in the far camera', () => {
  expect(
    shouldShowLabel({ id: 'stock:1', layer: 'stock', ratio: 1, highlighted: new Set() }),
  ).toBe(false)
  expect(
    shouldShowLabel({ id: 'action:BUY', layer: 'action', ratio: 1, highlighted: new Set() }),
  ).toBe(true)
  expect(
    shouldShowLabel({
      id: 'stock:1',
      layer: 'stock',
      ratio: 1,
      highlighted: new Set(['stock:1']),
    }),
  ).toBe(true)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend-advisor && npm test -- src/signalGraphLayout.test.ts`  
Expected: FAIL 模块不存在

- [ ] **Step 3: Write minimal implementation**

实现 `signalGraphLayout.ts`，导出上述全部符号。`layerCoordinates` 对 `action` 层：将节点按 `ACTION_ORDER` 索引排序（未知 label 放最后再 localeCompare）。

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd frontend-advisor && npm test -- src/signalGraphLayout.test.ts`  
Expected: PASS

- [ ] **Step 5: Commit**（默认跳过）

```bash
git add frontend-advisor/src/signalGraphLayout.ts frontend-advisor/src/signalGraphLayout.test.ts
git commit -m "feat: add layered coordinates and graph highlight helpers"
```

---

### Task 4: sigma 画布接到图学习页

**Files:**
- Modify: `frontend-advisor/package.json`（`npm install sigma graphology`，不要加 `@react-sigma/core` 或 forceatlas）
- Create: `frontend-advisor/src/components/SignalGraphView.tsx`
- Create: `frontend-advisor/src/components/SignalGraphView.test.tsx`
- Modify: `frontend-advisor/src/pages/SignalGraphPage.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Consumes: `fetchSignalGraphView`, `GraphViewPayload`, `GraphSignalItem`, `layerCoordinates`, `highlightFromNode`, `highlightFromEvidence`, `shouldShowLabel`, `edgeKey`
- Produces:
  - `<SignalGraphView payload={GraphViewPayload | null} error={string | null} highlight={HighlightState | null} onSelectNode={(id: string) => void} onResetView={() => void} />`
  - 容器 class `signal-graph-canvas`，高度 **520px**
  - 挂载 `new Sigma(graph, el, { ... })`；`useEffect` cleanup 调 `sigma.kill()`
  - 构图：`graphology` `Graph`；节点属性 `x,y,label,size,color,layer`；边属性 `size,color`（BUY 绿 / HOLD 黄 / SELL 红，沿用 `--color-buy` 思路：`#3d9a6a` / `#c4a35a` / `#c45c5c`）；`sample_count===0` 的边 `color` 带 alpha ≈ 0.12
  - `nodeReducer` / `edgeReducer`：有 highlight 时非高亮降透明；label 经 `shouldShowLabel`（`ratio` 用 `sigma.getCamera().ratio`，hovered 用 sigma 的 hover）
  - 点非 `action:*` 节点 → `onSelectNode(id)`
  - 空 payload（0 边）：文案「图还是空的，先生成或等自进化写入边」
  - `payload.truncated`：状态行「节点/边达上限，已按强度截断」
  - 页面：`refreshMeta` 增加 `fetchSignalGraphView()`，**try/catch 单独**，失败只 set `viewError`，摘要照常
  - 点节点：`setHighlight(highlightFromNode(id, payload.edges))`，**不**再请求 `/view`
  - 「返回全图」按钮：`setHighlight(null)` 并 `sigma.getCamera().animatedReset()`（若 API 无此方法则 `setState({ x: 0.5, y: 0.5, ratio: 1 })`）
  - 现有「生成」成功：`setHighlight(highlightFromEvidence(one.evidence || [], one.action))`，**禁止**为此再调 `fetchSignalGraphView`
  - `run('settle'|'batch'|'synthetic')` 已有 `refreshMeta()`，会重拉 `/view`（符合 spec）

jsdom 无 WebGL：`SignalGraphView.test.tsx` mock `sigma` 与 `graphology`：

```typescript
const kill = vi.fn()
vi.mock('sigma', () => ({
  default: class {
    constructor() {}
    getCamera() {
      return { ratio: 1, animatedReset: vi.fn(), setState: vi.fn() }
    }
    on() {}
    kill = kill
    getNodeDisplayData() { return { x: 0, y: 0 } }
  },
}))
```

断言：render 后 unmount 调用 `kill`；空态文案；truncated 文案。高亮逻辑已在 Task 3 覆盖，本测试不模拟像素。

`SignalGraphPage.test.tsx` 若不存在，在本任务为「生成后不请求 view」加一个最小测试：mock `fetchGraphSignal` 与 `fetchSignalGraphView`，点击生成后 `fetchSignalGraphView` 仍为 refreshMeta 的 1 次（mount），不再增加。若页面测试成本高，至少在 `SignalGraphView` 用 props `highlight` 渲染侧栏边权列表（从 `payload.edges` filter `highlight.edgeKeys`）。

侧栏：高亮时列出匹配边的 `src → dst`、`confidence`、`sample_count`。

- [ ] **Step 1: Install deps then write the failing component test**

```bash
cd frontend-advisor && npm install sigma graphology
```

确认 `package.json` **没有** `@react-sigma/core`、`graphology-layout-forceatlas2`。

写 `SignalGraphView.test.tsx`（先不实现组件 → FAIL）。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend-advisor && npm test -- src/components/SignalGraphView.test.tsx`  
Expected: FAIL 组件不存在

- [ ] **Step 3: Write minimal implementation**

1. `SignalGraphView.tsx`：`useRef<HTMLDivElement>`；`useEffect` 依赖 `payload` 重建 Graph+Sigma（高亮变化 **只** 调 `sigma.refresh()` 或 set reducer，不 `new Sigma`）。  
2. 颜色辅助：

```typescript
function actionColor(dst: string, alpha = 1): string {
  const hex = dst.endsWith('BUY') ? '#3d9a6a' : dst.endsWith('SELL') ? '#c45c5c' : '#c4a35a'
  if (alpha >= 1) return hex
  return hex // 冷启动边用 sigma 的 `color` + reducer opacity；或 `rgba` 若 sigma 接受
}
```

冷启动：`sample_count===0` 时边 `size` 更小且 reducer 里 `color` 透明度 0.12。

3. CSS：

```css
.signal-graph-canvas {
  height: 520px;
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface) 92%, transparent);
}
.signal-graph-toolbar {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin: 0.5rem 0;
}
```

4. `SignalGraphPage`：`refreshMeta` 内

```typescript
const [s, p, t, v] = await Promise.all([
  fetchSignalGraphSummary(),
  fetchSignalGraphPending(30),
  fetchSignalGraphSettled(30),
  fetchSignalGraphView().catch((err) => {
    setViewError(err instanceof Error ? err.message : String(err))
    return null
  }),
])
```

`viewError` 不要写进 `error`（避免挡摘要）。图画在「图状态」`</div>` 之后新 `diag-block` 标题「图」。

生成按钮现有 `setOne(await fetchGraphSignal(...))` 后追加：

```typescript
setHighlight(highlightFromEvidence(item.evidence || [], item.action))
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run:

```bash
cd backend && .venv/bin/python -m pytest tests/test_signal_graph_view.py tests/test_signal_graph_service.py -v
cd frontend-advisor && npm test -- src/signalGraphLayout.test.ts src/components/SignalGraphView.test.tsx
```

Expected: PASS  
手动：打开 `http://127.0.0.1:5174/signal-graph` 应看到全图；缩小有边网；点行业高亮；生成 600519 高亮 evidence 且 Network 面板无第二次 `/view`（除非 refreshMeta）。

- [ ] **Step 5: Commit**（默认跳过）

```bash
git add frontend-advisor/package.json frontend-advisor/package-lock.json frontend-advisor/src/components/SignalGraphView.tsx frontend-advisor/src/components/SignalGraphView.test.tsx frontend-advisor/src/pages/SignalGraphPage.tsx frontend-advisor/src/styles.css
git commit -m "feat: render the full signal graph with sigma.js"
```

---

## Spec coverage

| Spec 项 | Task |
|---------|------|
| 瘦全图 API、禁 ledger | 1 |
| 安全顶 8000/20000、truncated | 1 |
| 同 src-dst 合并 | 1 |
| GET `/view` | 2 |
| 分层 x/y、禁止力导向 | 3、4 |
| 点选高亮不重拉 | 3、4 |
| 生成用 evidence 不高亮以外的请求 | 4 |
| 标签 LOD | 3、4 |
| sigma + graphology、kill | 4 |
| 并行加载、图失败不挡摘要 | 4 |
| 空态 / 截断文案 | 4 |
| 冷启动边更淡 | 4 |
| 返回全图 | 4 |
