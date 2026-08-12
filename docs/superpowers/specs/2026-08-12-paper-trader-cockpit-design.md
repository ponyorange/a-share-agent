# 模拟盘交易员驾驶舱设计（二期）

## 目标

1. 提供独立页面 `/paper/trader`，作为全自动模拟盘交易员的**可视入口与驾驶舱**。
2. 一屏完成：启停/模式/风控、迷你持仓与净值、候选池、决策时间线、选中标的日 K（lightweight-charts）。
3. 后端提供聚合 `cockpit` 接口，与一期 worker 决策逻辑（`build_candidates`）保持一致。

## 已确认决策

| 项 | 决策 |
|----|------|
| 范围愿景 | 完整驾驶舱（控制台 + 迷你盘面 + 候选 + 决策 + 图表） |
| 路由 | `/paper/trader` 独立页 |
| 版式 | **A 上控下舱**：顶栏控制；下左候选/持仓，下右 K 线+决策时间线 |
| 架构 | 方案 1：聚合 cockpit API + 新页；启停复用现有 paper-trader API |
| 图表 | 内嵌 `lightweight-charts`；日 K 走现有 `/api/kline`（或 akshare 同源） |
| 轮询 | 盘中 15–30s；非盘中约 60s；写操作后立即刷新 |
| 一期关系 | 不改 worker 语义；本页为 UI + 只读聚合增强 |

## 非目标

- 独立 `paper-trader-worker`、异动事件驱动唤醒
- 委员会/策略版本接入候选池
- 分钟线深度盘口、多图联动、完整复刻 `/paper` 手动下单
- 浏览器 Visual Companion 依赖（实现不依赖）

## 架构

```text
Topbar「交易员」 /paper 短链
        │
        ▼
PaperTraderPage  (/paper/trader)
        │
        ├─ GET  /api/advisor/paper-trader/cockpit   （轮询）
        ├─ POST /api/advisor/paper-trader/start|pause|stop|resume
        ├─ PATCH /api/advisor/paper-trader
        ├─ GET  /api/advisor/paper-trader/decisions/{id}
        └─ GET  /api/kline?symbol=…&period=day      （选中标的）
                │
                ▼
        lightweight-charts 渲染 OHLCV
```

Worker 仍按一期 `run_due_paper_traders` 下单；驾驶舱只观测与配置。

## 页面布局

### 桌面

```text
┌─────────────────────────────────────────────────────────┐
│ 交易员  [status]  启/停/暂停/恢复   mode  interval  净值 │
│ 风控摘要（可展开编辑）                                     │
├──────────────────────┬──────────────────────────────────┤
│ 候选表               │ 选中标的日 K（lightweight-charts） │
│ 迷你持仓摘要         │ 决策时间线（可展开详情）           │
└──────────────────────┴──────────────────────────────────┘
```

### 移动

单列：控制折叠 → 候选 → K 线 → 决策 → 持仓摘要。

### 导航

- `BASE_NAV_LINKS` 增加 `{ to: '/paper/trader', label: '交易员' }`（紧挨「模拟盘」）
- `/paper` 页顶短链「打开交易员驾驶舱」
- `App.tsx` 注册路由

## API

### `GET /api/advisor/paper-trader/cockpit`

认证用户。响应形状：

```text
{
  session: object | { status: "stopped" },
  paper: {
    cash, equity, market_value,
    positions: [{ symbol, name, qty, cost, last }],  # 截断，默认 ≤20
    positions_count
  },
  candidates: [{
    symbol, name, direction, rule_score, graph_action,
    in_watchlist, in_recommendations, held_qty
  }],
  decisions: { page, page_size, total, items: [...] },
  meta: { is_trading, is_trading_day, server_now },
  errors?: { candidates?: str, paper?: str, decisions?: str }
}
```

实现要点：

- `candidates` 调用一期 `build_candidates(user_id)`（可设较短超时；失败写入 `errors.candidates`，其余字段仍返回）
- `paper` 用 `get_account(..., mark_to_market=False)` 以免拖慢轮询；可选后续加轻量 marks
- `decisions` 复用 `list_decisions`

启停/PATCH/单决策详情：沿用一期路由，行为不变（halted resume 须 `confirm_halt_resume`）。

## 前端行为

1. 挂载后拉 cockpit；按 `meta.is_trading` 选择轮询间隔。
2. 选中候选/持仓行 → 请求日 K → 喂给 lightweight-charts；无数据时占位。
3. 决策行展开：展示 `llm_actions` / `risk_blocked` / `orders_placed` / `skip_reason`；需要时再请求详情接口。
4. 风控/mode/interval：受控表单 → PATCH → 成功 toast「下轮生效」→ 刷新 cockpit。
5. halted 恢复：对话框确认后再调 resume。
6. 外链：完整模拟盘 `/paper`；个股亦可附 `explorerKlineUrl` 作为「新窗口看详细 K 线」。

## 依赖

- `frontend-advisor` 增加 `lightweight-charts`（npm）
- 不新增后端重型依赖

## 测试

- 后端：`cockpit` 在无会话时返回 stopped；有会话时含 candidates/decisions 键；候选失败时 `errors` 有值且 session 仍返回（monkeypatch）
- 前端：路由注册；启停按钮在 mock API 下调用正确路径；选中 symbol 触发 kline fetch（组件测或 RTL）
- 回归：现有 paper-trader / monitor 单测仍通过

## 验收

- 顶栏可进驾驶舱；完成启停与改 mode/风控
- 盘中轮询可见 stats/候选/决策变化
- 选中标的显示日 K
- 移动端可完成启停与查看决策
- `/paper` 与 Agent 工具不被破坏

## 实现顺序

1. `GET .../cockpit` + 单测  
2. `api.ts` 客户端 + 路由/导航  
3. `PaperTraderPage` 壳：顶栏启停 + 轮询  
4. 候选/持仓/决策列表  
5. lightweight-charts 日 K  
6. 风控编辑 + halted 恢复确认 + 移动布局打磨  

## 关联

- 一期：`docs/superpowers/specs/2026-08-10-paper-trader-agent-design.md`
- 计划：实现前另写 `docs/superpowers/plans/2026-08-12-paper-trader-cockpit.md`
