# 图学习信号整合设计

## 目标

1. 将 `.refers/graph_python` 收成顾问侧共享内核 `backend/app/advisor/signal_graph/`。
2. 提供研究闭环（C）：生成信号、到期结算、快照摘要、合成回测、前端研究页。
3. 并行增强今日关注 / 股票诊断（A）：响应附带 `graph_signal`，不替换多因子 `decide_action`。
4. Agent / 委员会消费图证据（B）：`get_graph_signal` / `run_graph_signals` / `settle_graph` / `get_graph_summary` 工具；委员会 snapshot 增加非关键 `signal_graph` collector。
5. 打板晋级可选附带短持有期图 evidence（P3），失败不影响主流程。

## 已确认决策

| 项 | 决策 |
|----|------|
| 架构 | 共享内核 + 多消费方；禁止各功能内复制图逻辑 |
| 分期 | P0→P1→P2→P3 |
| 判决 | 与多因子并行；`config.signal_graph.weight` 预留加权，默认 0 |
| 打板基础页 | 不改判决；仅晋级可选 evidence |
| 持久化 | Mongo `signal_graph_state` + 进程内缓存；单写者锁 |
| 参考源 | `.refers/graph_python` 保持 gitignore；正式代码在 `backend/` |

## 架构

```text
日线/基准/regime/行业/形态
        │
        ▼
context_builder → SignalEngine / FeedbackEngine (a_share_graph)
        │
        ▼
store (Mongo snapshot) ←→ service API
        │
        ├── /api/advisor/signal-graph/* + SignalGraphPage (C)
        ├── service.get_recommendations / get_advice 并行字段 (A)
        ├── agent tools (get/run/settle/summary) + committee collector (B)
        └── limitup_promote._attach_graph_evidence (P3)
```

## 配置

`backend/app/advisor/config.yaml` → `signal_graph`:

- `enabled`
- `attach_to_recommendations` / `attach_to_advice`
- `promote_evidence`
- `horizon_days` / `owner` / `weight`

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/advisor/signal-graph/summary` | 图状态摘要 |
| GET | `/api/advisor/signal-graph/signal` | 单票生成 |
| POST | `/api/advisor/signal-graph/signals` | 批量生成 |
| POST | `/api/advisor/signal-graph/settle` | 结算到期预测 |
| GET | `/api/advisor/signal-graph/pending` | 待结算列表 |
| GET | `/api/advisor/signal-graph/settled` | 已结算列表 |
| POST | `/api/advisor/signal-graph/synthetic` | 合成回测（不写生产图） |

## 数据约定

- `trade_tick`：按交易日在 meta `tick_by_date` 单调分配，可重放。
- ticker：内核用 `600519.SH`；产品侧仍用 6 位代码。
- 推荐归档 `rec_snapshots` items 可含 `graph_signal`（含 `prediction_id`）。
- 冷启动样本不足时输出 `HOLD` 且可能无 `prediction_id`。

## 非目标

- 用图替换 `scoring.py` / `rule_optimize`
- 实盘下单、分钟级信号
- 打板池用图重排
- 多进程并发写同一张图

## 验收

- P0：研究页与 API 可生成 / 结算 / 合成回测；快照可恢复
- P1：推荐与诊断含 `graph_signal`；关闭 attach flag 后无该字段路径
- P2：Agent 可调 `get_graph_signal`；委员会 snapshot items 含 `signal_graph`
- P3：晋级 picks 可选 `graph_signal`；异常时带 `error` 不阻断
