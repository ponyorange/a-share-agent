# 打板加载进度与打板晋级思考展示设计

## 目标

1. 基础面板「打板」加载时展示**真实阶段进度**（拉涨停池 / 资金流 / 组装），避免长时间只显示笼统「正在拉取」。
2. Agent「打板晋级」研判时展示**阶段进度 + 模型思考（若有）**，完成后仍输出结构化候选表。
3. 复用仓库已有 SSE 模式（龙虎榜 / 今日关注），不引入 WebSocket。

## 已确认决策

| 项 | 决策 |
|----|------|
| 范围 | 两处都做：基础打板 + Agent 打板晋级 |
| 架构 | 方案 A：服务端 SSE 推送真实阶段 |
| 打板轮询 | 盘中约 10 秒轮询可继续用短缓存 GET；首屏与手动刷新走 stream |
| 晋级缓存 | 沿用现有 `(user_id, trade_date)` 短缓存；命中则直接 `done` + `from_cache` |
| 思考内容 | 有 `reasoning` / `reasoning_content` 则推 `thinking`；无则仅阶段文案 + 可选 JSON token |
| 文案 | 研究观察、不保证次日涨停、非投资建议与下单指令 |
| 非目标 | 拉长后端缓存 TTL、前端假进度、WebSocket、改基础打板表格列 |

## 架构

```text
【基础打板】
LimitUpPage 首屏/刷新
    → GET /api/akshare/limit-up/stream?force=
    → meta → progress* → done(payload) | error
    盘中轮询（可选）仍 GET /api/akshare/limit-up（6s 缓存）

【打板晋级】
LimitUpPromotePage 加载/刷新
    → GET /api/advisor/limitup/promote/stream?force=
    → progress* → thinking* → (token*) → done(picks) | error
    无 DeepSeek Key → 首包或连接前 403 / error 事件
```

## 后端：打板 SSE

### 路由

- `GET /api/{source}/limit-up/stream`（与现有 `limit-up` 同鉴权/来源约定）
- Query：`force`（bool，默认 false）

### 事件

| event | 字段 | 说明 |
|-------|------|------|
| `meta` | `date?`, `force`, `cached?` | 开始；若整包缓存命中可标 `cached` |
| `progress` | `phase`, `message`, `done?`, `total?` | 阶段更新 |
| `done` | 与现有 `get_limit_up` 相同 payload | 完整结果 |
| `error` | `detail` | 失败 |

### phase 约定

| phase | message 示例 |
|-------|----------------|
| `pool` | 正在拉取涨停池 / 炸板池… |
| `fund_flow` | 正在补充主力资金流…（可带 done/total） |
| `build` | 正在组装连板天梯… |
| `cache` | 命中短缓存…（可选，几乎瞬间 done） |

### 实现要点

- 在 `backend/app/limitup.py` 增加 `iter_limit_up_events(*, force=False)`：
  - 短缓存命中：`meta(cached)` → `done`
  - 未命中：拆出现有 `get_limit_up` 步骤并 yield progress；`enrich_fund_flow` 支持进度回调或批间 yield
- 现有同步 `get_limit_up` 保留，供轮询 / 晋级上下文 / 首页等调用
- 进程内 6s 缓存逻辑不变：stream 完成后仍写入同一 `_cache`

## 后端：打板晋级 SSE

### 路由

- `GET /api/advisor/limitup/promote/stream?force=`
- 需登录；未配置 DeepSeek → HTTP 403 或 SSE `error`（与现有 GET 文案一致：「请先配置 DeepSeek API Key」）
- 保留现有 `GET/POST /api/advisor/limitup/promote`（非流式）以兼容；前端改为走 stream

### 事件

| event | 字段 | 说明 |
|-------|------|------|
| `progress` | `phase`, `message` | `pool` / `model` / `parse` 等 |
| `thinking` | `delta` | 模型推理增量（可拼接） |
| `token` | `delta` | 可选：最终 JSON 文本增量 |
| `done` | 与 `generate_promote_picks` 相同结构 | `picks` / `summary` / `from_cache` 等 |
| `error` | `detail` | 失败 |

### 实现要点

- `limitup_promote.py`：`iter_promote_events(user_id, *, force=False)`
  - 校验凭证 → 取封板上下文（可复用 `build_promote_context`）→ 缓存命中则直接 done
  - 否则 `build_chat_model(..., streaming=True)`，`stream`/`astream` 收集内容
  - 从 chunk 的 `additional_kwargs` / `response_metadata` 提取 reasoning（若模型提供）；推 `thinking`
  - 流结束后复用现有 JSON 解析 + `filter_picks_against_context`，再 `done`
- 无 reasoning 的模型：不强制失败，仅展示阶段进度；有 token 可展示折叠「模型输出」

## 前端

### 打板页 `LimitUpPage`

- 新增 `streamLimitUp(force, handlers, signal?)`（模式对齐 `streamLeaderboard`）
- 首屏与手动刷新走 stream；状态行展示 `message` 与可选 `done/total`
- 盘中自动刷新：优先继续短轮询 GET（减少 SSE 开销）；若实现成本低也可统一 stream
- AbortController：离开页面 / 重复刷新时中止

### 打板晋级页 `LimitUpPromotePage`

- `streamLimitUpPromote(force, handlers, signal?)`
- UI：
  - 顶部阶段状态（取池 / 模型研判 / 解析）
  - 可折叠「思考过程」面板：拼接 `thinking` delta；无则隐藏或显示「该模型未返回思考内容」
  - `done` 后渲染表格（与现有一致）
- 无 Key：展示错误 + 链到 DeepSeek 配置

## 测试

- 后端：`iter_limit_up_events` mock 池与资金流，断言 progress 顺序与 done 字段；缓存命中无重复拉取
- 后端：`iter_promote_events` mock 流式 chunk（含/不含 reasoning），断言 thinking 拼接与 picks 校验；无 Key 抛错；缓存 hit 不调模型
- 前端：打板页展示进度文案；晋级页展示 thinking 与 picks；无 Key 文案

## 非目标与约束

- 不拉长打板 6s 缓存作为「离开后下次秒开」方案（另议）
- 不在基础打板页嵌入晋级入口（保持现状）
- Prompt / 页面继续统一风险提示文案
