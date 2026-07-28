# Agent 盯盘定时任务设计（一期）

## 目标

1. 用户可通过对话让 Agent 创建「盯盘」定时任务：监控收藏 / 持仓 / 指定标的，在交易时段按规则检测行情。
2. 条件触发后**自动发邮件**（无需用户确认），不下单。
3. Agent 面板提供「定时任务」管理页：查看、暂停、继续、删除；对话也可停止/删除。

## 已确认决策

| 项 | 决策 |
|----|------|
| 一期动作 | 仅邮件提醒；预留二期模拟盘自动下单 |
| 判定方式 | **结构化规则**轮询；LLM 介入为二期 |
| LLM 介入（二期方向） | 非每轮调用；创建时询问；如每 15 分钟 + 异动（短时涨跌超阈值）再接入 LLM |
| 调度架构 | 独立 `monitor-worker` + Mongo 任务表（与投委会 RQ 分离） |
| 邮件 | 已验证邮箱；盯盘告警**直接发送**，绕过 chat `confirm` |
| 创建交互 | Agent 先提问补齐字段，用户确认后再落库 |
| 暂停/继续/删除 | 工具与管理页均可；无需 confirm |

## 非目标（一期）

- LLM 盘中决策 / 简报
- 模拟盘或真实券商自动下单
- 任意自然语言「万能定时任务」（仅盯盘规则类）
- 价格提醒推送 App / 短信

## 架构

```text
对话 / 管理页
    │
    ▼
HTTP / Agent tools  ──►  Mongo agent_monitor_jobs
                              │
                              ▼
                    monitor-worker（交易时段循环）
                              │
              get_last_quote + trading_session
                              │
                    规则命中 + 冷却去重
                              │
                              ▼
                         send_email（SMTP）
```

要点：

- Worker 只处理 `status=running`；`paused` / `stopped`（删除前可先停）不扫。
- 删除为硬删除或 `status=deleted` 软删（实现选硬删除即可，列表不展示）。
- 冷却：默认同一 `(job_id, symbol, rule_id)` 30 分钟内不重复发信；记在任务文档 `alert_cooldowns` 或子集合。

## 数据模型

集合：`agent_monitor_jobs`  
索引：`user_id + status`、`user_id + updated_at`。

```text
{
  _id,
  user_id: str,
  title: str,
  status: "running" | "paused",
  scope: "watchlist" | "portfolio" | "symbols",
  symbols: [str],          # scope=symbols 时必填；其它 scope 运行时展开
  rules: [
    {
      id: str,             # uuid 短码
      type: "price_below" | "price_above" | "day_chg_below" | "day_chg_above",
      value: float,        # 价格或涨跌幅小数（0.03=3%）
      hint: str | null,    # 邮件内建议文案，如「关注买入」
    }
  ],
  note: str | null,        # 用户原意摘要
  notify_email: str,       # 创建时快照已验证邮箱
  cooldown_sec: int,       # 默认 1800
  # 二期预留（一期写默认值，不启用）
  llm_enabled: false,
  llm_interval_sec: 900,
  llm_anomaly_abs_chg: 0.03,
  created_at, updated_at,
  last_run_at: datetime | null,
  last_alert_at: datetime | null,
  alert_cooldowns: { "symbol:rule_id": iso_datetime },
  last_error: str | null
}
```

展开标的：

- `watchlist` → 当前收藏代码列表（每次 tick 重新读，收藏变更自动生效）
- `portfolio` → 当前真实持仓代码
- `symbols` → 任务内固定列表

上限建议：每用户最多 **20** 条任务；单任务 `symbols` 最多 **50**（watchlist/portfolio 按实际数量，超额 tick 截断并记 `last_error`）。

## 规则求值

对每个 symbol 取 `get_last_quote`：

| type | 条件 |
|------|------|
| `price_below` | `price <= value` |
| `price_above` | `price >= value` |
| `day_chg_below` | `day_chg_pct <= value`（value 为负或小值，如 -0.03） |
| `day_chg_above` | `day_chg_pct >= value` |

缺价则跳过该标的。命中且冷却已过 → 发邮件 → 更新 cooldown / `last_alert_at`。

## Worker

- 入口：`backend/app/advisor/monitor/worker.py`（或同级模块）
- 循环：休眠 20–30s；`trading_session().is_trading` 为 false 时拉长休眠（如 60s）不跑规则
- Compose：新增 `monitor-worker` 服务，镜像同应用，命令跑 worker
- 并发：单 worker 即可；按 job 顺序处理，单 tick 内限制总 quote 次数（如 200）防打爆行情源

## 邮件

- 复用 `app/mail.py` `send_email`
- 主题示例：`【盯盘】{title} · {symbol} 触发 {rule}`
- 正文：标的、现价、涨跌幅、规则、hint、任务名、免责声明
- **不走** `send_chat_summary_email` 的 confirm 流程
- 创建任务时校验用户 `email_verified_at`；未验证 → 400 / 工具错误，引导个人资料页

## API

前缀 `/api/advisor/monitor`，需登录。

| Method | Path | 说明 |
|--------|------|------|
| GET | `/jobs` | 当前用户任务列表 |
| POST | `/jobs` | 创建（body 含 title/scope/symbols/rules…） |
| POST | `/jobs/{id}/pause` | 暂停 |
| POST | `/jobs/{id}/resume` | 继续 |
| DELETE | `/jobs/{id}` | 删除 |

错误：无邮箱 400；超上限 400；非法 rule 422。

## Agent 工具

| Tool | 行为 |
|------|------|
| `list_monitor_jobs` | 列表摘要 |
| `create_monitor_job` | 落库；缺关键字段返回 `needs_clarification`；成功返回 job 摘要 |
| `pause_monitor_job` | 按 id 或 title 模糊唯一匹配 |
| `resume_monitor_job` | 同上 |
| `delete_monitor_job` | 同上 |

系统提示补充：

- 创建前必须问清 scope / 规则 / 标题；复述后用户确认再调用 `create_monitor_job`
- 盯盘告警邮件自动发送，无需 confirm；不下单
- 停止/删除用 pause 或 delete 工具
- 二期 LLM 字段勿在一期启用

## 前端

- `TopbarNav` `AGENT_NAV_LINKS` + `MobileAgentMoreMenu` 增加「定时任务」→ `/agent/jobs`
- 新页 `MonitorJobsPage.tsx`：表格/卡片列表 + 暂停/继续/删除
- `api.ts`：`fetchMonitorJobs` / `createMonitorJob` / `pauseMonitorJob` / `resumeMonitorJob` / `deleteMonitorJob`

## 测试

- 规则求值单测（价格/涨跌、冷却）
- 创建缺邮箱失败
- pause 后 worker 逻辑跳过（可单测 `should_run`）
- 前端列表操作 smoke

## 实现顺序建议

1. 数据模型 + CRUD API + 单测  
2. Worker + 邮件 + compose  
3. Agent 工具 + SYSTEM_PROMPT  
4. 管理页 + 导航  
5. 文档 / README 运维说明  

## 二期预留（不实现）

- `llm_enabled` 周期 + 异动触发 LLM，邮件附「建议买/卖/观望」
- 可选：邮件后模拟盘下单
