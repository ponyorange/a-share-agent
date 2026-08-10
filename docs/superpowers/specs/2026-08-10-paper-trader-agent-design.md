# 模拟盘全自动交易员 Agent 设计（MVP）

## 目标

1. 提供**盘中全自动**模拟盘交易员：用户启停后，系统按固定节奏看盘、决策并下单，无需逐笔确认。
2. 决策为**混合双轨**：默认信号优先，可切换为 LLM 优先；硬风控代码闸门不可被模型覆盖。
3. 候选池为**当日推荐 ∪ 自选股**，并始终纳入当前模拟盘持仓（以便卖出）。
4. 可观测与可复盘：每轮决策日志、熔断/日终邮件、现有模拟盘页查看成交；连续交易日应有合理换手（非空转），不承诺收益。

## 已确认决策

| 项 | 决策 |
|----|------|
| 自主程度 | 全自动交易员（用户主要看日志/日报，很少逐笔确认） |
| 决策形态 | 混合：规则分 / SignalGraph + LLM 结构化定仓 |
| 双轨模式 | 账户级 `signal_first`（默认）/ `llm_first` 可切换 |
| MVP 范围 | 盘中自动执行闭环；专用控制台二期 |
| 候选池 | `recommendations ∪ watchlist`（+ 模拟盘持仓进上下文） |
| 节奏 | 约每 5–15 分钟一轮；默认 `interval_sec=600` |
| 默认风控 | 激进：单票 ≤25%、总仓 ≤90%、最多 10 只；保留日亏损熔断与涨跌停附近禁成 |
| 调度载体 | 扩展现有 `monitor-worker`，不新建独立 trader worker |
| 下单确认 | 仅 `paper_trader` 执行路径免确认；对话 Agent 仍需 `confirm=true` |
| 成功标准 | 闭环可靠 + 决策/拦截可复盘 + 信号存在时有非零换手 |
| UI | MVP：API + Agent 工具启停；看板复用现有模拟盘页 |

## 非目标（本期）

- 专用交易员大屏 / 复杂仪表盘
- 真实券商或实盘下单
- 全市场自由选股（池外标的）
- 改变现有盯盘任务「只告警不下单」语义
- 任意 crontab / 多时区
- App 推送 / 短信
- 强制每轮必须成交（禁止为「有换手」而突破硬风控）

## 架构

```text
启停 API / Agent 工具 /（可选）定时任务页入口
              │
              ▼
     Mongo paper_trader_sessions   ← 每用户最多 1 个会话文档
              │
              ▼
        monitor-worker（已有常驻进程）
          └─ run_monitor_tick 内调用 run_due_paper_traders()
              │
              ▼
   paper_trader/cycle.py（结构化流水线，非自由聊天 ReAct）
     1. 展开候选：recommendations ∪ watchlist ∪ paper positions
     2. 行情 + 规则分 / SignalGraph（按可用性）
     3. LLM 结构化决策 JSON（买/卖/持有、数量或占比、理由）
     4. 硬风控闸门
     5. place_order(..., source="paper_trader")
     6. 写入 paper_trader_decisions；推进 next_run_at
              │
              ▼
   熔断/日终邮件 + 现有模拟盘页 + decisions API 复盘
```

要点：

- **不新建 worker**：挂在现有 `monitor-worker`，运维模型与盯盘一致（须常开，含非交易时段以便调度字段推进/日终任务）。
- **与盯盘分离**：不用 `kind=watch` 规则邮件路径承载下单；`run_at` 仍只跑主 Agent + 邮件、不下单。
- **对话 Agent 默认仍需确认**；免确认仅限 trader cycle 内部调用 `place_order`。
- Worker 内 trader 与盯盘评估**异常隔离**：trader 单轮超时（建议 120s）不得拖死整个 monitor tick。

## 数据模型

### `paper_trader_sessions`

每用户至多一条会话文档（再 `start` 时复用并重置状态，而非无限新建）。

```text
{
  user_id: str,
  status: "running" | "paused" | "stopped" | "halted",
  mode: "signal_first" | "llm_first",          # 默认 signal_first
  interval_sec: int,                           # 默认 600；合法范围 300–900
  risk: {
    max_single_position: 0.25,
    max_total_exposure: 0.90,
    max_positions: 10,
    max_trades_per_day: 30,
    max_daily_loss_pct: 0.05,                  # 相对日初净值
    lot_size: 100,
    block_limit_board: true
  },
  candidate: { sources: ["recommendations", "watchlist"] },
  notify_email: str | null,                    # 已验证邮箱快照；可空
  next_run_at: datetime | null,
  last_run_at: datetime | null,
  last_error: str | null,
  halt_reason: str | null,
  day_anchor: str | null,                      # YYYY-MM-DD 上海日历，用于日切 stats
  equity_day_open: float | null,               # 日初净值，供熔断
  stats_today: {
    trades: int, buys: int, sells: int,
    blocked: int, llm_calls: int, rounds: int
  },
  created_at, updated_at
}
```

索引：`user_id` 唯一；`status + next_run_at`（worker 扫到期 running）。

状态语义：

| status | 行为 |
|--------|------|
| `running` | 盘中到期执行 cycle |
| `paused` | 不执行；保留配置 |
| `stopped` | 不执行；`next_run_at=null`；持仓不动 |
| `halted` | 风控/连续失败熔断；需显式 resume 才能再下单 |

### `paper_trader_decisions`

每轮一条，供复盘与验收「有换手感」。

```text
{
  user_id, session_id, run_id,
  started_at, finished_at,
  mode,
  candidate_symbols: [str],
  signals_summary: object,       # 各标的分数/图信号摘要
  llm_actions: [                 # 解析后的结构化意图
    { symbol, side, qty | target_weight, reason }
  ],
  risk_blocked: [                # 被硬风控否决
    { symbol, side, reason }
  ],
  orders_placed: [               # 实际成交引用
    { trade_id, symbol, side, qty, price }
  ],
  skip_reason: str | null,       # 整轮空过（非交易时段/无候选/坏 JSON 等）
  error: str | null
}
```

索引：`user_id + started_at` 降序；`session_id + started_at`。

### 成交来源

`paper_trades.source = "paper_trader"`，与对话 Agent 的 `source="agent"`、一键买入等区分。

### 配置（`config.yaml`）

新增 `paper_trader:` 段，提供默认 `interval_sec`、`risk`、候选上限、LLM 超时、单轮 wall timeout、连续零成交提示阈值、连续 LLM 失败熔断阈值。会话文档可覆盖 interval/mode/risk。

## 单轮流水线

仅当：`status=running` 且交易时段且 `next_run_at <= now`。

1. **建上下文**  
   候选 = 推荐池 ∪ 自选 ∪ 当前模拟盘持仓。合计上限（默认 40）；超额按信号/分数优先级裁剪，**持仓优先保留**。

2. **特征与信号**  
   批量行情 + 规则评分；SignalGraph 可用则附带 `action/scores`。单标的失败标记 `signal_unavailable`，不导致整轮崩溃。

3. **按 mode 生成意图（LLM 输出严格 JSON schema）**  
   - **明确方向（代码先打标，再喂给 LLM）**：  
     - 买向：`SignalGraph.action=BUY`，或规则综合分 ≥ `buy_threshold`（`config.yaml`）。  
     - 卖向：`SignalGraph.action=SELL`，或规则综合分 ≤ `sell_threshold`。  
     - 其余标为中性；无图信号时仅用规则分。  
   - `signal_first`：仅对已打「买向/卖向」的标的允许非 HOLD；中性标的只可 HOLD；**禁止池外**。  
   - `llm_first`：候选池内可自由买/卖/持有；**仍禁止池外**；提案后必须过硬风控。

4. **硬风控闸门（纯代码）**  
   校验：单票占比、总仓、持仓只数、T+1 可卖量、日成交笔数、日亏损熔断、涨跌停附近禁止、现金充足、整手。否决写入 `risk_blocked`，该意图不下单。触发日亏损熔断则会话 → `halted`。  
   **涨跌停附近**：行情标记涨停/跌停则禁成；否则若可得涨停价/跌停价，现价距该价相对幅度 &lt; 0.5% 则禁成；再否则用当日涨跌幅启发式（主板 |chg|≥9.5%，创业板/科创 |chg|≥19.5%）禁成。

5. **执行**  
   意图数量：优先使用 `qty`；若仅有 `target_weight`，则  
   `qty = floor(equity * target_weight / last_price / lot_size) * lot_size`（卖出另受可卖量约束）。  
   通过闸门后调用 `place_order(..., source="paper_trader")`，价格缺省为最新价。部分失败不回滚已成功成交；记入 `orders_placed` 与 `error`。

6. **收尾**  
   持久化 `paper_trader_decisions`；`next_run_at = now + interval_sec`；更新 `stats_today`；必要时发熔断邮件。

### 「有换手感」轻量保障

若连续 N 轮（默认 3，可配）在「候选中至少 1 只已打买向或卖向标」时仍零成交，下一轮给 LLM 的系统提示加强「允许小仓试错」；**仍不得突破硬风控**，不设强制随机买入。

### 错误与超时

| 情况 | 行为 |
|------|------|
| LLM 超时 / JSON 非法 | 本轮 `skip_reason`，会话保持 `running` |
| 连续 LLM 失败达阈值 | `halted` + 可选邮件 |
| 单轮 wall clock 超时（120s） | 中止本轮，记 error，推进 `next_run_at` |
| 非交易时段 | 不执行 cycle；日切时可跑日终邮件逻辑 |
| 无已验证邮箱 | 只落库，不发信 |

## API

前缀：`/api/advisor/paper-trader`（需登录）。

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/` | 当前会话 + 今日 stats |
| POST | `/start` | 创建或恢复为 `running`（可带 mode/interval/risk） |
| POST | `/pause` | → `paused` |
| POST | `/stop` | → `stopped`，清空 `next_run_at`；不改持仓 |
| POST | `/resume` | `paused` → `running`；`halted` → `running` 须 `confirm_halt_resume=true` |
| PATCH | `/` | 改 mode / interval / risk（下轮生效） |
| GET | `/decisions` | 分页决策日志 |
| GET | `/decisions/{id}` | 单轮详情 |

### Agent 工具

镜像启停、查状态、查最近决策。`pause`/`stop` 无需 confirm；`start` 与从 `halted` resume 使用 `confirm=true`（或等价口头确认后再调）。**不**通过对话工具暴露免确认的随意 `paper_place_order` 新语义。

## 邮件

- **熔断**：进入 `halted` 时立即一封（有 `notify_email` 时）。
- **日终简报**：当日 rounds、成交笔数、拦截数、净值变化摘要；无邮箱则仅落库。  
  日终触发：worker 在交易日结束后检测到 `day_anchor` 需结算且当日曾 `running`/`halted`。

## Worker 接入

在 `run_monitor_tick` 中于盯盘评估之后（或独立 try/except 块）调用 `run_due_paper_traders()`：

- 查询 `status=running` 且 `next_run_at <= now` 的会话。
- 每用户串行；全局可限制本 tick 最多处理 N 个会话，避免打满 LLM。
- 与盯盘统计字段分开计数（日志可增加 `paper_trader_runs`）。

## 测试

必测：

1. 风控：超单票/总仓/持仓数、T+1、涨跌停附近、日笔数、日亏损 → 拦截或 `halted`。
2. mode：`signal_first` 与 `llm_first` 均拒绝池外标的。
3. 调度：非交易时段不落单；`interval` 正确推进；`paused`/`stopped`/`halted` 不下单。
4. 执行：`source=paper_trader` 成交入账；决策文档字段完整。
5. 回归：盯盘 watch 仍不下单；对话 `paper_place_order` 无 `confirm` 不成交。

## 验收

- 可一键 start/pause/stop；熔断后须显式 resume。
- 至少连续 2 个交易日存在决策日志；有拦截场景时可复盘 `risk_blocked`。
- 至少一个「候选含买向/卖向标」的交易日出现非零模拟盘成交（不规定胜率）。
- 现有盯盘与对话确认下单行为不被破坏。

## 实现顺序（供后续 plan 拆分）

1. 会话 store + 配置默认值 + HTTP 启停 API  
2. 硬风控模块 + 单测  
3. cycle 流水线（候选/信号/LLM schema/下单/决策落库）  
4. 接入 `monitor-worker` + 超时隔离  
5. 邮件（熔断 + 日终）+ Agent 工具  
6. 换手提示与回归测试  

## 二期方向（不在本期实现）

- 交易员专用控制台（时间线、风控编辑 UI）
- 更细事件驱动（异动唤醒）与独立 trader worker
- 策略版本绑定 / 委员会输出接入候选
