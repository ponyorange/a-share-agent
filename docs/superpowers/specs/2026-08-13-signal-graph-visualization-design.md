# 图学习可视化设计

## 目标

在顾问前端「图学习」页画出**完整真实 SignalGraph**：一次加载全图（瘦节点/边），用 WebGL 缩放拖拽查看整体；点节点或输入代码在全图上高亮局部，并与当前单票信号对齐。

## 已确认决策

| 项 | 决策 |
|----|------|
| 位置 | 现有 `/signal-graph` 页，「图状态」下方 |
| 渲染 | **sigma.js v3 + graphology**（WebGL）；不用 SVG / Cytoscape / 力导向 |
| 布局 | 前端按层赋坐标：市场 / 行业 / 形态 / 个股 → BUY / HOLD / SELL |
| 全图 | 默认画出全部情境→动作边；冷启动（`sample_count==0`）用更淡的线，不隐藏 |
| 点选 | 不换图。高亮该节点及其连到三个动作的边，其余降透明；镜头移近该节点 |
| 代码框 | 沿用「生成」；用返回的 `evidence` 在全图上高亮证据边与当前动作，不重拉全图 |
| 只读 | 不在图上改边权；不另存可视化副本 |
| 数据 | `store.load_runtime` 内存图；**只序列化节点+边瘦字段**，禁止带 ledger / snapshot |

## 性能硬限制

当前约 742 点 / 2370 边，WebGL 足够画全图。约束针对「别把错误的东西做大」：

1. **禁止**把 `dump_snapshot`、pending/settled/unresolved 预测作为可视化 payload。
2. 节点字段仅 `id, layer, label`；边字段仅 `src, dst, layer, confidence, sample_count, last_tick`。
3. 安全顶：节点 **8000**、边 **20000**。超出则按 `strength = sample_count * (0.5 + abs(confidence))` 截断，`truncated: true`，状态行提示。当前规模必须 `truncated: false` 且返回全边。
4. 后端 O(E) 扫内存图一次构图；不按请求做力导向、不写盘。
5. 前端 **禁止** ForceAtlas / 每帧重布局。坐标在构图时算一次；hover / 高亮只改 reducer（颜色/大小/标签），不重建 Graph。
6. **标签 LOD**：默认远景只显示 `action:*` 与当前高亮节点的 label；放大或 hover 再出其余文字。禁止 700+ 个 DOM 标签。
7. 全图只请求 **一次**；点选不重拉；生成信号用 `evidence` 高亮。刷新摘要/结算成功后再拉一次 `/view`。
8. `/view` 与 pending/settled **并行**；图接口失败不得拖垮摘要。
9. Sigma 容器固定高度（约 520px），WebGL context 在路由卸载时 `kill()`，避免泄漏。

## 架构

```text
store.load_runtime (内存)
        │
        ▼
view_graph()  →  瘦 nodes[] + edges[]
        │
        ▼
GET /api/advisor/signal-graph/view
        │
        ▼
graphology Graph + 分层 x/y
        │
        ▼
sigma.js WebGL（缩放/拖拽）
        │
    点节点 ──► 高亮邻域（不请求）
    生成信号 ──► evidence 高亮（不请求 /view）
    返回全图 ──► 清除高亮，镜头复位
```

## 依赖

仅加在 `frontend-advisor`：

- `sigma`（v3）
- `graphology`
- `graphology-types`（若 sigma peer 需要）

不引入 `@react-sigma/core`（自己 `useRef` 挂载即可，少一层封装）。不引入 `graphology-layout-forceatlas2`。

## API

`GET /api/advisor/signal-graph/view`

无必填查询。可选 `max_nodes` / `max_edges` 仅供测试，生产默认用安全顶，前端不传。

```json
{
  "truncated": false,
  "node_count": 742,
  "edge_count": 2370,
  "nodes": [
    {"id": "regime:bull", "layer": "market", "label": "bull"}
  ],
  "edges": [
    {
      "src": "regime:bull",
      "dst": "action:BUY",
      "layer": "market",
      "confidence": 1.2,
      "sample_count": 8,
      "last_tick": 10
    }
  ]
}
```

- `layer`：`market` | `industry` | `pattern` | `stock` | `action`
- `dst` 仅为 `action:BUY|HOLD|SELL`
- 不返回 `owner` / `scope_id` / `attrs` / `commits`
- 空图或未启用：200 + 空数组，不是 500

同一 `src-dst` 若因不同 `scope_id` 有多条边，可视化 **合并**：`sample_count` 相加，`confidence` 取样本加权平均，`last_tick` 取 max。合并后边数 ≤ 原始边数，避免三条 scope 叠成看不清的平行线。

## 前端

`frontend-advisor/src/components/SignalGraphView.tsx` 挂在「图状态」下。

分层坐标（构图时写到 graphology 节点属性）：

| layer | x |
|-------|---|
| market | 0 |
| industry | 1 |
| pattern | 2 |
| stock | 3 |
| action | 4 |

列内按 `label` 排序均匀分布 y∈[0,1]；动作三节点固定顺序 BUY / HOLD / SELL 且节点更大。

边：颜色随 `dst`（买绿 / 持黄 / 卖红）；`size` 由 `|confidence|` 与 `sample_count` 映射；`sample_count==0` 透明度约 0.12。

交互：

- 滚轮缩放、拖拽平移（sigma 默认）。
- 点情境节点：高亮该点 + 到 BUY/HOLD/SELL 的边；侧栏列出这三条的 confidence / sample_count。
- 「返回全图」：清高亮、镜头复位到包含全部节点的相机。
- 「生成」成功：按 `evidence[].src` 与 `action:*` 高亮；镜头对准该股票节点（若图中尚无该 stock 节点，只高亮 evidence 里已有的情境节点，不补点）。
- 空态：「图还是空的，先生成或等自进化写入边」。

jsdom 不能跑 WebGL：把「payload → graphology + x/y」抽成纯函数单测；组件对 Sigma 做 mock，不断言像素。

## 错误处理

- `/view` 失败：摘要照常；图画区域显示错误，可重试。
- 生成失败：现有错误条；高亮保持不变。
- `truncated: true`：状态行「节点/边达上限，已按强度截断」，不弹窗。

## 测试

- 后端：瘦字段；无 snapshot/ledger 键；当前夹具全量返回且 `truncated` 为假；超过安全顶才截断；同 src-dst 合并。
- 前端：分层 x 正确；LOD 规则（远景 action 有 label、stock 无）；高亮集合由 node_id / evidence 算出；卸载调用 kill。

## 非目标

- 力导向布局、3D、边编辑
- WebSocket 实时刷新
- 可视化接到今日关注 / 诊断页
- 把预测台账画进图里

## 验收

- 打开图学习页能看到全图（边数与摘要 `edge_count` 在未截断时一致），网络响应不含 pending 列表。
- 缩小能看见整体边网；放大或 hover 才出个股文字。
- 点行业节点：全图仍在，仅该节点邻域高亮，并列出到三个动作的边权。
- 输入 `600519` 生成后，证据边高亮且标出当前动作，不再请求 `/view`。
- 离开页面后 WebGL 上下文释放（无泄漏警告）。
