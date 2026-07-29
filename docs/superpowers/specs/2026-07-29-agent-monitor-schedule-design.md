# Agent 定时任务调度增强设计（盯盘 + 定点）

## 目标

1. **修复**：任务必须按约定时间自动启动；不再「创建即 running、却无到点激活」。
2. **调度语义**：创建时区分一次性 / 重复，以及盯盘窗口 vs 定点执行。
3. **可观测**：任务日志控制台（打开后轮询）；列表展示距下次运行倒计时（运行中不展示）。

## 已确认决策

| 项 | 决策 |
|----|------|
| 调度实现 | 增强现有 `monitor-worker`（`next_run_at` 轮询），不另挂 APScheduler |
| 任务类型统一 | 同一套「定时任务」：`watch`（盘中盯盘）+ `run_at`（定点执行） |
| 一次性盯盘结束 | 目标交易日 **15:05（北京时间）后** → `completed` |
| 定点重复日历 | 创建时可选 `trading_days`（仅交易日）或 `everyday`（自然日） |
| 定点执行体 | **调用主 Agent 一轮**（带任务提示词）+ 结果邮件到已验证邮箱；失败写入日志与 `last_error` |
| 日志 | 独立 run/log 集合；页面抽屉打开后约 **3s** 轮询 |
| 倒计时 | 基于 `next_run_at`；`status=running` 的盯盘进行中不展示 |

## 非目标（本期）

- 任意 crontab 表达式 / 多时区
- App 推送 / 短信
- 单会话内消息分页
- 改变规则类型或 LLM 看盘阈值语义（仍沿用现有规则 + llm_* 字段）

## 问题根因（现状）

当前创建后立刻 `status=running`，worker **仅在** `trading_session().is_trading` 时扫任务求值；无 `start_at` / once / recurring / `next_run_at`。  
「昨晚约明天盯盘」无法表达延迟激活；worker 未部署时更表现为「从未开始」。

## 架构

```text
对话 Agent / 管理页
        │
        ▼
Mongo agent_monitor_jobs   （scheduled | running | paused | completed | failed）
        │
        ▼
monitor-worker 短间隔循环
   ├─ 激活：next_run_at <= now 且 status=scheduled → 进入 running / 执行 run_at
   ├─ watch + running：仅 is_trading 时规则/LLM 求值（同现网）
   ├─ watch + once：过 end_at(当日 15:05) → completed，写日志
   ├─ run_at：到期执行一次 Agent+邮件 → once 则 completed；recurring 则推进 next_run_at
   └─ 写 agent_monitor_job_logs
```

Worker 非盘中也要跑（建议仍 25–60s sleep），以便激活「明天 09:00」类任务；盘中对 `watch` 求值频率可保持现状。

## 数据模型

### `agent_monitor_jobs` 新增/调整字段

```text
kind: "watch" | "run_at"
repeat: "once" | "recurring"
calendar: "trading_days" | "everyday"   # recurring 必填；once 可选（默认 trading_days）
tz: "Asia/Shanghai"                     # 固定

# 调度锚点（北京时间语义，存 UTC datetime）
anchor_date: "YYYY-MM-DD" | null        # once 的目标日；recurring 可空
run_time: "HH:MM" | null                # run_at 必填，如 "09:00"
                                        # watch 可空，默认按交易日 09:15 进入可求值窗口
end_time: "HH:MM"                       # watch 默认 "15:05"

next_run_at: datetime | null            # 下次应激活/执行的时间（倒计时）
end_at: datetime | null                 # once watch 的硬结束时刻
started_at: datetime | null             # 本轮进入 running 的时间
completed_at: datetime | null

status: "scheduled" | "running" | "paused" | "completed" | "failed"

# run_at 专用
prompt: str | null                      # 交给主 Agent 的任务说明（整理行情/推荐等）
```

保留现有：`scope/symbols/rules/llm_*/notify_email/cooldown_sec/note/last_*`。  
`watch` 仍要求规则（或显式允许仅 LLM 看盘）；`run_at` 可不要求行情规则，以 `prompt` 为主。

### 状态机

```text
create → scheduled
scheduled + next_run_at due → running   (watch 进入窗口 / run_at 开始执行)
running + pause → paused
paused + resume → scheduled 或 running  （若仍在窗口内则 running，并重算 next_run_at）
watch once + now > end_at → completed
run_at once 成功执行完 → completed
run_at recurring 执行完 → scheduled（已写好下一次 next_run_at）
不可恢复错误 → failed（可手动 pause/delete；是否允许 resume 由实现定：默认需重建）
delete → 硬删任务 + 可选保留最近日志 N 天
```

### 创建示例映射

| 用户说法 | kind | repeat | calendar | 调度 |
|----------|------|--------|----------|------|
| 明天盯收藏 | watch | once | trading_days | 下一交易日 09:15→running；15:05→completed |
| 每天盯收藏 | watch | recurring | trading_days | 每个交易日窗口内 running；收盘后回 scheduled 并设次日 next_run_at |
| 明天 9 点整理行情发邮件 | run_at | once | — | next_run_at=该日 09:00；跑完 completed |
| 每天 9 点推行情 | run_at | recurring | 用户选 trading_days 或 everyday | 每次跑完推进下一次 09:00 |

### `next_run_at` 计算要点

- 时区：`Asia/Shanghai`。
- `trading_days`：跳过非交易日（复用 `is_trading_day`）。
- `watch` recurring：若当前已在交易时段且 status 应为 running，则倒计时隐藏；收盘后 `next_run_at` = 下一交易日 09:15。
- `run_at`：`anchor_date + run_time` 或「下一个匹配日的 run_time」；错过超过宽限（如 10 分钟）则记日志并跳到下一周期（recurring）或 `failed`/`completed`（once，实现选：记 missed 后 completed 并告警日志）。

### 迁移

旧任务无新字段时：

- `kind=watch`, `repeat=recurring`, `calendar=trading_days`
- `status=running` 保持；补 `next_run_at=null`（已在窗口内）或下一交易日 09:15
- 行为与「每个交易日盯盘」对齐，避免一夜清空用户任务

## Worker 行为

每 tick：

1. **到期激活**：`status=scheduled` 且 `next_run_at <= now`  
   - `watch` → `running`，写日志 `activated`；若已过 `end_at` 则直接 `completed`  
   - `run_at` → 执行定点流水线（见下），不进入长时间 running（可短暂 `running` 防重入，结束后按 repeat 转状态）
2. **盯盘求值**：`kind=watch` 且 `status=running` 且 `is_trading` → 现有规则/LLM/邮件逻辑；touch `last_run_at`；写摘要日志（可降频，如每 N 分钟一条 heartbeat）
3. **盯盘收盘**：`watch` + `running` + `now >= end_at`（once）或非交易且越过当日 end_time（recurring）→ once:`completed`；recurring:`scheduled` + 计算下次 `next_run_at`
4. **暂停任务**：永不激活、不求值

### `run_at` 执行流水线

1. 校验已验证邮箱 / DeepSeek 配置  
2. 以任务 `prompt`（+ 只读上下文：日期、scope 摘要可选）调用主 Agent 非流式或短超时流式聚合  
3. 将回答发邮件（主题含任务 title）  
4. 写日志 `run_ok` / `run_failed`；更新 `last_run_at` / `last_error`  
5. 推进状态与 `next_run_at`

并发：同一 `job_id` 加短锁或 `running` 占用，防止双 worker（若未来多实例）重入；单实例部署可先用「执行中标记 + 超时回收」。

## 日志

集合：`agent_monitor_job_logs`

```text
{
  job_id, user_id,
  ts, level: "info"|"warn"|"error",
  event: "created"|"activated"|"tick"|"alert"|"email"|"run_ok"|"run_failed"|"completed"|"paused"|"resumed"|"missed",
  message: str,
  detail: object | null   # 截断体积
}
```

- 索引：`(job_id, ts desc)`、`(user_id, ts desc)`
- 保留：建议每任务最近 500 条或 30 天（实现可选后台裁剪）
- API：`GET /monitor/jobs/{id}/logs?after_ts=&limit=100`
- 前端：任务行「日志」→ 抽屉；打开时拉取，**3s 轮询**（页面不可见可暂停）

## 前端（`/agent/jobs`）

- 列表字段：标题、kind/repeat 标签、status、`next_run_at` **倒计时**（前端每秒刷新；`running` 的 watch 不显示倒计时，可显示「运行中」）
- 操作：暂停 / 继续 / 删除 / 打开日志（创建仍主要靠对话；页面临时可不提供表单创建）
- Agent 系统提示与 `create_monitor_job`：必须收集并复述 kind/repeat/calendar/时间，写入新字段；禁止再「只建立刻 running 的无调度任务」

## API 变更摘要

- `POST /monitor/jobs`：body 增加调度字段；默认 status=`scheduled`，计算 `next_run_at`/`end_at`
- `GET /monitor/jobs`：返回新字段 + `next_run_at`
- `GET /monitor/jobs/{id}/logs`：新增
- pause/resume/delete：resume 时重算 `next_run_at`

## 测试要点

- once watch：跨日激活 → 盘中求值 → 15:05 后 completed  
- recurring watch：收盘变 scheduled，下一交易日再激活  
- run_at once / recurring × trading_days / everyday  
- 错过窗口的 once/recurring 行为  
- 旧任务迁移默认  
- 日志写入与 logs API  
- 倒计时：running 不展示

## 成功标准

1. 「昨晚创建明天盯盘」在次日交易时段自动 `running` 并出现 `last_run_at` / 日志 `activated`+`tick`（worker 在线前提下）。  
2. 一次性盯盘当日结束后自动 `completed`，不再盘中误跑。  
3. 定点 9:00 任务能在约定时刻触发 Agent+邮件（或失败可在日志看见）。  
4. 管理页可见倒计时与可轮询日志。
