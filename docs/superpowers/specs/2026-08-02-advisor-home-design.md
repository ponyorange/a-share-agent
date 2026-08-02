# 次日顾问基础面板 · 市场首页

日期：2026-08-02  
状态：已确认设计（待用户审阅 spec 正文）

## 目标

为顾问前端「基础」面板增加真正的 **市场首页**：打开应用默认进入 `/`，一眼看到指数、趋势、情绪、涨跌分布、连板/涨停、热点题材与闸门一句话。  
首页只做市场全景，不做交易决策台、不做持仓汇总、不做 Agent 动态。

## 已确认决策

| 项 | 决策 |
|----|------|
| 定位 | 市场全景首页（market dashboard） |
| 范围 | 只做市场：指数 / 趋势情绪 / 涨跌分布 / 连板涨停 / 热点题材 / 闸门一句话 |
| 布局 | **A · 市场驾驶舱**（上二下二网格） |
| 默认路由 | `/` = 首页；今日关注迁到 `/recommendations` |
| 首屏模块 | 上述 6 类全部要 |
| 加载 | 各模块独立请求：骨架先出，谁先到谁展示 |
| 实现 | **方案 1**：前端拼装现有接口 + 一个行业强度薄接口 |

## 非目标

- 不在首页塞今日关注列表、持仓、模拟盘、Agent 会话
- 不做 SSE / 推送式行情中台（可后续迭代）
- 不做完整 `/api/advisor/home` 聚合大包（与独立加载冲突）
- 不改闸门计算引擎语义；首页只消费已有/缓存摘要
- 不重做打板 / 今日闸门整页（仅链接触达）

## 信息架构（A 驾驶舱）

```
┌─────────────────────────┬─────────────────────────┐
│ 主要指数（featured）      │ 趋势 + 情绪 + 闸门一句话   │
│                         │ → 查看今日闸门 /regime    │
├─────────────────────────┼─────────────────────────┤
│ 涨跌家数 / 涨跌分布       │ 连板高度 / 晋级 / 涨停    │
│                         │ + 热点题材 Top           │
│                         │ → 打板 /limitup          │
└─────────────────────────┴─────────────────────────┘
```

原则：

- 文案中文，沿用 `regimeCopy` 风格（闸门/情绪/趋势枚举不直接裸奔英文）
- 单块失败只影响该块；整页无全局阻塞 spinner
- 窄屏：同一四块改为单列，顺序为 指数 → 趋势情绪闸门 → 涨跌分布 → 连板/热点

## 路由与导航

| 路径 | 页面 |
|------|------|
| `/` | 新 `HomePage`（市场驾驶舱） |
| `/recommendations` | 原 `RecommendationsPage`（今日关注） |
| 其余基础路由 | 不变 |
| `/agent/*` | 不变 |

导航 `BASE_NAV_LINKS`：

1. `{ to: '/', end: true, label: '首页' }`
2. `{ to: '/recommendations', label: '今日关注' }`
3. 其后保持现有顺序（股票诊断、持仓、收藏…）

行为约定：

- 面板切换「基础」→ `navigate('/')`（落到新首页）
- 今日闸门页「仍要看今日关注 / 查看今日关注」→ `/recommendations`（含 `regime_override` 查询参数时带到该路径）
- 站内其它写死 `to="/"` 且语义为今日关注的链接，一并改为 `/recommendations`

## 数据与接口

各块 **并行、互不 await**：

| 模块 | 数据源 |
|------|--------|
| 指数 | 现有 `/api/market`（`featured` 指数：名称、点位、涨跌幅） |
| 趋势 / 情绪 / 闸门一句话 | `/api/advisor/regime/current`；该块允许偏慢，但不得阻塞其它块。若实测仍拖累体验，可追加轻量 `GET /api/advisor/regime/summary`（内部 `get_regime_for_gate(allow_stale=True)`），首页改用 summary |
| 涨跌分布 | 优先 market 返回的涨跌家数字段；若无，用 regime `metrics.breadth` / 相关 evidence，并标注「摘要」 |
| 连板 / 晋级 / 涨停概况 | `fetchLimitUp` + regime metrics（`max_board`、`promotion_rate`、`limit_up_count` 等） |
| 热点题材 | **新增** `GET /api/advisor/market/sectors?top=8`：包装已有 `fetch_industry_strength_map`，返回 Top N（行业名、涨跌幅或强度分、可选排名） |

前端建议结构：

- `pages/HomePage.tsx` + 可选 `components/home/*` 四块瓦片
- `api.ts`：`fetchMarket`（若尚未封装）、`fetchHomeSectors`
- 复用 `regimeCopy` 做趋势/情绪/闸门短文案

### 失败态

- 每块：`loading 骨架` → `成功内容` / `本块失败 + 可选重试`
- 禁止因某一接口失败导致整页空白
- 非交易日：仍展示最近可得摘要（与闸门归档语义一致），在页头用一行 meta 标明日期/时段即可

## 测试

- `HomePage.test.tsx`：mock 各接口；一块 hanging 时其它块仍渲染
- `TopbarNav.test.tsx` / `App.test.tsx`：`/` 首页、`/recommendations` 今日关注
- 闸门页链接测试：关注入口指向 `/recommendations`
- 后端：`/api/advisor/market/sectors` 单测（map 空 / Top N 截断 / 鉴权）

## 验收标准

1. 登录后打开顾问默认进入市场首页（`/`）
2. 首屏可见四区骨架，并逐步填满 6 类信息（指数、趋势情绪、涨跌、连板涨停、热点、闸门一句话）
3. 任意单块慢/失败不挡住其余块
4. 导航可到「今日关注」`/recommendations`，原功能不回归
5. 从首页可一键进入 `/regime`、`/limitup`

## 实现备注

- 工作量以 frontend-advisor 为主；后端仅 sectors 薄路由（及可选 regime summary）
- 不引入新的设计体系；沿用现有 `page` / `diag-block` / `meta-line` 视觉语言
- `.superpowers/` 头脑风暴产物不入库
