# Agent 盯盘二期设计：规则增强 + Agent 看盘（LLM）

## 目标

1. **规则盯盘增强**
   - 用户未给出涨跌/价格阈值时，对话 Agent 根据知识库建议规则，确认后落库。
   - 新增主力资金突增/突减即时邮件告警（与价/涨跌规则并行）。
2. **Agent 看盘（LLM）**
   - 任务可开启 `llm_enabled`：约每 15 分钟，或标的涨跌异动超过约 3% 时，综合大盘/行情/资金/新闻政策 + 用户知识，判断买/卖/观望。
   - **仅买/卖发邮件**；观望不发；**不下单**。

## 已确认决策

| 项 | 决策 |
|----|------|
| 落地动作 | 仅邮件，不下单（含模拟盘） |
| 交付范围 | 规则增强 + Agent 看盘一次交付 |
| 架构 | 扩展现有 `monitor-worker` 双通道（方案 1） |
| 知识 | 默认注入账户「必选知识」；任务可再绑 `knowledge_ids` |
| 规则与看盘 | **并行**：规则/资金命中即时邮件；看盘另通道研判 |
| 看盘发信 | 仅 `buy` / `sell`；`hold` 不发 |
| 看盘实现 | 预拉上下文 + **单次结构化 LLM**（非完整 ReAct） |
| 资金异动 | 相对近 N 日均值放大 ≥ K **或** 净流入占比 ≥ 阈值，任一满足 |
| LLM 凭证 | 用户已配置的 DeepSeek（`resolve_llm_credentials`） |

## 非目标

- 模拟盘 / 真实券商自动下单
- Worker 内完整 Agent 工具循环（ReAct）
- App / 短信推送
- 投委会 RQ 队列承载盯盘（仍保持独立 monitor-worker）

## 架构

```text
monitor-worker tick（交易时段）
        │
        ├─ 通道 A：规则告警
        │     price_* / day_chg_* / flow_spike_*
        │     命中 + 冷却过 → 即时邮件
        │
        └─ 通道 B：Agent 看盘（llm_enabled）
              触发：间隔 ≥ llm_interval_sec
                    或 |Δ日内涨跌| ≥ llm_anomaly_abs_chg
              预拉上下文包 + 必选知识(+绑定)
              单次 LLM → JSON(buy|sell|hold)
              仅 buy/sell → 研判邮件
```

创建侧（对话 Agent）：补齐规则/是否看盘 → 用户确认 → `create_monitor_job`。

## 数据模型

集合仍为 `agent_monitor_jobs`（向后兼容一期文档）。

### 新增 / 启用字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `rules[].type` | enum | 新增 `flow_spike_in` \| `flow_spike_out` |
| `rules[].value` | float | 资金规则：净流入占比阈值，默认 `0.10` |
| `rules[].mult` | float? | 相对近窗均值放大倍数，默认 `3` |
| `rules[].window_days` | int? | 均值窗口交易日数，默认 `5` |
| `llm_enabled` | bool | 开启通道 B |
| `llm_interval_sec` | int | 默认 `900` |
| `llm_anomaly_abs_chg` | float | 默认 `0.03` |
| `knowledge_ids` | [str] | 额外知识 ID；必选知识运行时始终注入 |
| `last_llm_at` | datetime? | 上次通道 B 完成时间 |
| `llm_symbol_baselines` | {symbol: float} | 用于异动比较的日内涨跌基线 |
| `last_llm_error` | str? | 通道 B 错误（与规则错误分开） |

一期字段（`cooldown_sec`、`alert_cooldowns`、`notify_email` 等）保持不变。  
看盘邮件冷却建议使用独立键前缀，如 `llm:{symbol}`，默认复用 `cooldown_sec`。

### 上限

- 每用户任务数、symbols 上限同一期（20 / 50）。
- `knowledge_ids` 建议最多 **8** 条；注入正文总长度受知识库既有字数纪律约束（超长截断）。
- 每用户每 tick 通道 B 最多 **1** 次；单次最多评估 **10** 只标的（优先异动标的）。

## 通道 A：规则与资金

### 既有规则

`price_below` / `price_above` / `day_chg_below` / `day_chg_above` 行为同一期。

### 资金异动

对每个 symbol 取主力净流入与成交额（复用/抽取 `market_context` 或东财个股资金流能力；缺数据则跳过该规则，不误报）。

触发（`flow_spike_in` 看流入侧，`flow_spike_out` 看流出侧），**任一**满足即可：

1. **相对**：`|当日主力净流入| ≥ mult × |近 window_days 日均值|`  
   - 若均值绝对值过小（实现定下限，如接近 0），则本条相对条件视为不满足，仅依赖占比。
2. **占比**：`|主力净流入 / 成交额| ≥ value`

命中且 `symbol:rule_id` 冷却已过 → 发即时告警邮件（主题/正文标明资金异动与数值）→ 更新 cooldown。

## 通道 B：Agent 看盘

### 触发

任务 `llm_enabled=true` 且处于交易时段时，若：

1. `last_llm_at` 为空或 `now - last_llm_at ≥ llm_interval_sec`，或  
2. 监控池内存在 symbol 满足异动：  
   - 有 baseline：`|day_chg_pct - baseline| ≥ llm_anomaly_abs_chg`  
   - 无 baseline：`|day_chg_pct| ≥ llm_anomaly_abs_chg`

则进入本轮看盘（每用户每 tick 至多一次）。

无 LLM 凭证：跳过，写 `last_llm_error`，不发信。

### 上下文包（调用前组装）

| 块 | 来源 |
|----|------|
| 知识 | `format_always_knowledge_section` + `knowledge_ids` 正文 |
| 大盘 | 主要指数点位/涨跌（现有指数拉取） |
| 标的 | `get_last_quote` + 资金摘要 |
| 新闻/政策 | 个股新闻 + 指数/联播类摘要（现有 unstructured 同类函数，限条数） |
| 任务元数据 | title、note、scope |

禁止在本通道内跑完整工具环；需要的数据一律预拉。

### LLM 契约

- 客户端：`build_chat_model(user_id, streaming=False)`（温度偏低，如 0.2）。
- 输出必须为 JSON（解析失败 = 本轮失败，不发信）：

```json
{
  "symbols": [
    {
      "symbol": "510300",
      "action": "buy|sell|hold",
      "confidence": 0.0,
      "rationale": "简短理由",
      "catalysts": ["相关新闻或政策要点"]
    }
  ],
  "market_note": "大盘一句话"
}
```

- 仅对 `buy` / `sell` 发研判邮件（可按标的分封或合并一封，实现选合并一封优先，避免刷屏）。
- 成功后更新 `last_llm_at`、`llm_symbol_baselines`；清除或覆盖 `last_llm_error`。
- 超时/异常：不发信，记录 `last_llm_error`。

## 创建交互（对话）

1. 问清监控范围（收藏/持仓/代码）、是否要 Agent 看盘、任务名。
2. 用户未给阈值 → 读取必选知识（必要时 `load_knowledge`）建议价/涨跌/资金规则 → 复述确认。
3. 开启看盘时说明：间隔与异动默认值、仅买/卖邮件、不下单；确认用户已配置 DeepSeek。
4. 用户确认后调用 `create_monitor_job`（可带 `llm_enabled`、`knowledge_ids`、规则含 `flow_spike_*`）。

## API

前缀不变：`/api/advisor/monitor`。

| Method | Path | 变更 |
|--------|------|------|
| GET | `/jobs` | 回传 LLM/资金相关字段与 `last_llm_*` |
| POST | `/jobs` | body 支持 `llm_enabled`、`llm_interval_sec`、`llm_anomaly_abs_chg`、`knowledge_ids`；规则含 flow 类型 |
| pause/resume/delete | 不变 | |

校验：开启 `llm_enabled` 时建议校验已配置 LLM（无 Key → 400）；非法 rule → 400/422。

## Agent 工具

扩展 `create_monitor_job` 参数（或 JSON 字段）以支持 LLM/知识/资金规则；`list_monitor_jobs` 摘要含看盘开关与最近 LLM 状态。

系统提示（规则 24 扩展）：

- 未给阈值时据知识库建议规则再确认。
- 「看盘」→ `llm_enabled`，仅买/卖发信、不下单。
- 资金/规则即时告警与看盘并行。

## 前端

`MonitorJobsPage`：

- 展示看盘开/关、`last_llm_at`、`last_llm_error`。
- 规则摘要识别资金类型。
- 说明文案：可对话创建「规则告警 + Agent 看盘」。

## 测试

- 资金规则：相对倍数命中、占比命中、缺数据跳过、冷却。
- 通道 B：间隔触发、异动触发、hold 不发、buy/sell 发、无凭证跳过、坏 JSON 不发。
- Store/API：`knowledge_ids` 上限、`llm_enabled` 无 Key 拒绝（若实现校验）。
- 前端：列表字段 smoke（可选）。

## 实现顺序

1. 资金流快照 + `flow_spike_*` 求值与邮件  
2. 通道 B：上下文包、LLM 调用、研判邮件、baselines  
3. Store/API/Agent 工具与 SYSTEM_PROMPT  
4. 管理页展示  
5. 单测与本地冒烟（`run_monitor_tick`）

## 与一期 Spec 关系

- 一期：`docs/superpowers/specs/2026-07-28-agent-monitor-jobs-design.md` 仍描述基础规则通道。  
- 本文覆盖一期「二期预留」及额外的资金规则、知识库估阈值创建流程。  
- 实现时以本文为准扩展 `monitor/` 模块，不另起 worker 进程。
