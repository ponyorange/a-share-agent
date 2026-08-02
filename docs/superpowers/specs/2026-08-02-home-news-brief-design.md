# 首页新闻热点与 Agent 解读

日期：2026-08-02  
状态：已确认（实现计划见 `docs/superpowers/plans/2026-08-02-home-news-brief.md`）

## 目标

在顾问「市场首页」驾驶舱下方增加 **新闻热点模块**：左侧展示联播/政策/舆论等资讯，右侧展示 Agent 对资讯的解读，并标出可能影响的 **相关板块** 与 **相关股票**（需求原文「睡眠板块」按相关板块理解）。

## 已确认决策

| 项 | 决策 |
|----|------|
| 布局 | 驾驶舱下方整行双栏：左资讯、右解读；窄屏先资讯后解读 |
| 解读生成 | 默认读缓存；用户点「刷新解读」才现调 Agent |
| 资讯来源 | 联播 + 宏观政策 + 指数情绪 + 联网舆情 + 题材热点（全要） |
| 缓存粒度 | **共享新闻源** + **每人独立 Agent 解读缓存** |
| 实现路线 | 方案 1：首页双栏 + `home/news` + `home/news-brief`（含 refresh） |

## 非目标

- 不做盘中实时舆情推送 / SSE 长推资讯
- 不在首页嵌入完整 Agent 对话工作台
- 不自动下单或改持仓
- 不把解读表述为投资建议（沿用页脚免责声明）
- 不做全站共享「一份解读」替代个人缓存

## 信息架构

```
[ 市场驾驶舱四宫格 ]

┌──────────────────────────┬──────────────────────────┐
│ 今日资讯                  │ Agent 解读                │
│ 更新时间                  │ [刷新解读]  status        │
│ · 联播                    │ 一句话市场含义            │
│ · 宏观政策                │ · 分点解读 ≤5             │
│ · 指数情绪                │ 相关板块 chips            │
│ · 题材热点                │ 相关股票 代码/简称/理由   │
│ · 联网舆情（可选）         │                          │
└──────────────────────────┴──────────────────────────┘
```

原则：

- 左栏与驾驶舱瓦片一样 **独立加载**，互不 await
- 右栏无缓存时 **不自动** 调 LLM（避免首屏烧 Token）
- 空组（来源失败或未开启联网）直接不渲染该分组标题

## 数据模型

### 共享新闻包 `home_news_daily`

按 `trade_date`（有效交易日）一份：

| 字段 | 说明 |
|------|------|
| `trade_date` | YYYY-MM-DD |
| `as_of` | 生成时间 |
| `groups` | `{ cctv, macro, index_sentiment, sectors, web }` 每组 `{ ok, source?, error?, items[] }` |
| `items[]` | 统一尽量含 `title` / `summary?` / `published_at?` / `url?` / `tags?` |

来源映射：

| 组 | 来源 |
|----|------|
| `cctv` | `fetch_market_cctv_news` |
| `macro` | `fetch_macro_china_snapshot` 摘要化 |
| `index_sentiment` | `fetch_index_news_sentiment`（API 不可用则 `ok=false` 空组） |
| `sectors` | 首页热点行业 Top + 可选短讯 |
| `web` | 仅在刷新解读且用户开启 `web_research` 时，由任务写入/更新；首页只读展示 |

### 用户解读 `home_news_briefs`

按 `(user_id, trade_date)`：

| 字段 | 说明 |
|------|------|
| `status` | `idle` \| `running` \| `ready` \| `failed` |
| `summary` | 一句话 |
| `bullets` | string[]，≤5 |
| `sectors` | `{ name, reason }[]` |
| `symbols` | `{ symbol, name, reason }[]` |
| `updated_at` | ISO 时间 |
| `error` | 失败信息（可选） |
| `news_as_of` | 生成时所用新闻包时间（可选） |

## API

均需登录。

| 方法 | 路径 | 行为 |
|------|------|------|
| GET | `/api/advisor/home/news` | 返回当日共享新闻包；若无则同步拉取结构化源（不含重 LLM）并落库 |
| GET | `/api/advisor/home/news-brief` | 返回当前用户当日简报；无记录时可 `{ status: "idle" }` |
| POST | `/api/advisor/home/news-brief/refresh` | 若已 `running` 则返回现任务；否则置 `running` 并后台生成 |

刷新前置：

- 已配置 DeepSeek Key，否则 400/明确错误引导 `/agent/settings`
- 输入 = 共享新闻包（可截断）+ 可选用户知识摘要
- 输出须为可解析 JSON（字段同 brief）；股票代码尽量用工具/白名单校验，无法核验则标注「待核实」或不输出该票
- `web_research` 开启时可在任务内补充联网要点写入共享包 `web` 组；未开启则跳过

前端：

- `HomePage` 增加 `HomeNewsSection`（或等价组件）
- `fetchHomeNews` / `fetchHomeNewsBrief` / `refreshHomeNewsBrief`
- 刷新后短轮询 GET（间隔 ~2s，超时给出失败提示）直到非 `running`

## 失败态

- 左栏单组失败：隐藏或灰显该组，其它组照常
- 左栏整包失败：错误 + 重试，不拖垮驾驶舱
- 右栏 `idle`：空态文案 + 刷新按钮
- 右栏 `running`：禁用刷新 + 「生成中…」
- 右栏 `failed`：展示 `error` + 可再刷新
- 未开联网：不展示「联网舆情」分组

## 测试

- 后端：新闻包聚合（部分源失败仍返回其它组）；brief 读写；refresh 状态机（idle→running→ready/failed）；无 DeepSeek Key 拒绝刷新
- 前端：左栏独立于驾驶舱；无 brief 不自动 POST；刷新中按钮禁用；ready 后渲染板块/股票

## 验收标准

1. 首页驾驶舱下可见双栏资讯/解读模块  
2. 左栏可展示多来源分组（有数据的组）  
3. 右栏默认不自动调用 LLM；点刷新后进入生成中并最终展示解读与相关板块/股票  
4. 单源或解读失败不导致整页空白  

## 实现备注

- 复用 `advisor.agent.unstructured` 与现有 LLM settings / `build_chat_model` 路径  
- 后台刷新可用线程/简易 job（对齐 rec refresh 的「断线可续」精神，首版轮询即可）  
- 视觉沿用 `home-tile` / `meta-line`，不新开设计体系  
