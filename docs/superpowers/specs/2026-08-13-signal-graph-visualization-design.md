# 图学习可视化设计

## 目标

在顾问前端「图学习」页画出**真实 SignalGraph**：默认鸟瞰学得比较熟的边，点节点或输入代码进入局部子图，并与当前单票信号对齐。性能是硬约束：浏览器永不接收全图。

## 已确认决策

| 项 | 决策 |
|----|------|
| 位置 | 现有 `/signal-graph` 页，「图状态」下方 |
| 形态 | 分层 SVG：市场 / 行业 / 形态 / 个股 → BUY / HOLD / SELL |
| 鸟瞰 | 按学习强度取 Top 边，默认 100、硬顶 120 |
| 点选 | 展开该节点连到三个动作的全部边（单节点硬顶 80 条） |
| 代码框 | 沿用现有「生成」；用返回的 `evidence` 画证据子图并高亮当前动作 |
| 只读 | 不在图上改边权；不另存可视化副本 |
| 图库 | 不用 Cytoscape / vis / 力导向；固定分层布局 |
| 数据 | 进程内 `store.load_runtime` 已缓存的图；只序列化切片 |

## 性能硬限制

当前规模约 742 节点 / 2370 边，会继续涨。约束如下，实现不得放宽：

1. **禁止**把全图、`dump_snapshot`、ledger 预测列表作为可视化 payload。
2. 鸟瞰：最多 **120 条边、200 个节点**；响应 JSON 目标 **< 80KB**。
3. 焦点子图：最多 **80 条边**。个股 `evidence` 通常远小于此，原样使用，截断时保留 contribution 最大的边。
4. 后端在内存图上 **O(E) 扫描 + 部分排序** 取 Top N；2 万边以内应在数十毫秒内完成。单测用 1 万边夹具断言 overview 仍 ≤120 边。
5. 前端 **无力学模拟、无每帧重布局**。hover 只改 CSS / stroke，不重算坐标。
6. 鸟瞰与焦点 **分请求**：点节点只拉焦点，不重拉鸟瞰。
7. 单票「生成」**不再扫全图**：用已有 `generate_signal` 的 `evidence` 画局部。
8. 页面进入时图视图与 pending/settled 列表并行；图接口失败不得拖垮摘要。

超出上限时响应带 `truncated: true` 与 `total_edges`，前端显示「仅展示 Top N / 共 M 条」。

## 架构

```text
store.load_runtime (内存)
        │
        ▼
view_overview(limit)     view_focus(node_id)
        │                         │
        └──────────┬──────────────┘
                   ▼
        GET /api/advisor/signal-graph/view
                   │
                   ▼
        SignalGraphView（分层 SVG）
                   │
    点节点 ──► 再请求 view?node_id=
    生成信号 ──► 用 evidence 本地构图，不打 view
```

## API

`GET /api/advisor/signal-graph/view`

| 查询 | 说明 |
|------|------|
| （无） | 鸟瞰 Top 边 |
| `limit` | 1–120，默认 100 |
| `node_id` | 焦点：该情境节点 → `action:*` 的边 |

响应（鸟瞰与焦点同一形状）：

```json
{
  "mode": "overview | focus",
  "focus_id": "industry:电子 | null",
  "truncated": false,
  "total_edges": 2370,
  "shown_edges": 100,
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

节点 `layer` 仅限：`market` | `industry` | `pattern` | `stock` | `action`。  
边 `dst` 仅为 `action:BUY|HOLD|SELL`。不返回 `owner` / `scope_id` / `attrs` / `commits`（可视化用不到）。

### 鸟瞰排序

对每条边计算 `strength = sample_count * (0.5 + abs(confidence))`。  
先排除 `sample_count == 0` 的冷启动边；若有效边 < 20，再按 `|confidence|` 回填。  
取 `strength` 最大的 `limit` 条。节点集合 = 这些边的端点（含三个动作节点，即使某动作暂无入边也画出 BUY/HOLD/SELL）。

### 焦点

`node_id` 必须是已存在的情境节点（非 `action:*`）。返回其全部指向动作的边，超过 80 条时按 `strength` 截断并 `truncated: true`。未知 `node_id` → 400。

## 前端

`frontend-advisor/src/components/SignalGraphView.tsx` 挂在 `SignalGraphPage`「图状态」下。

- 五列固定 x：market / industry / pattern / stock / action。
- 列内按 `label` 排序垂直堆叠；列过多时该列内部滚动，整图允许横向滚动。
- 边：直线或二次贝塞尔；`strokeWidth` 由 `|confidence|` 映射到 1–5px；色随动作（买绿 / 持黄 / 卖红，沿用现有 badge 语义色）。
- 点击情境节点：请求 `view?node_id=`，替换为焦点图；提供「返回鸟瞰」（用已缓存的 overview，不强制重拉）。
- 现有代码框「生成」成功后：用 `one.evidence` 构图（`src` / `dst=action:{action}` / contribution 映射为粗细），高亮 `one.action`；不调用 `/view`。
- 空态：无熟边时文案「还没有学熟的边（样本为 0 的冷启动边已隐藏）」。
- 移动端同一组件，横向滑动，不另做力导向。

## 错误处理

- `/view` 图未启用或空图：200 + 空 `nodes/edges`，不是 500。
- 焦点 `node_id` 非法或不存在：400，前端保留上一幅图并显示错误。
- 生成失败：现有错误条；图保持鸟瞰。
- 截断：状态行提示，不弹窗。

## 测试

- 后端：排序取 Top N；`sample_count==0` 默认剔除；1 万边夹具 overview ≤120；焦点只含该 `src`；payload 无 snapshot/ledger 字段。
- 前端：fixture 鸟瞰可渲染；点击发出 `node_id`；生成后用 `evidence` 画图且不请求 `/view`。

## 非目标

- 下载或绘制全图
- WebGL / 3D / 力导向 / 缩放动画库
- 在图上编辑、删除、手工加边
- WebSocket 实时刷新
- 把可视化接到今日关注 / 诊断页（本迭代只在图学习页）

## 验收

- 打开图学习页，鸟瞰出现且边数 ≤120，网络响应不是全量 snapshot。
- 点一个行业节点，只看到该节点到 BUY/HOLD/SELL 的边。
- 输入 `600519` 生成后，图切到证据子图并标出当前动作。
- 「返回鸟瞰」立即回到上一幅 Top 图。
