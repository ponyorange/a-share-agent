# 股票收藏（自选）设计

## 目标

1. 用户可收藏 / 取消收藏标的，并在独立模块查看带行情的收藏列表。
2. 「今日关注」「标的诊断」提供空心/实心星快捷操作。
3. Agent 可查看收藏列表，并直接帮用户收藏 / 取消收藏（无需二次确认）。

## 已确认决策

| 项 | 决策 |
|----|------|
| 存储 | 独立 Mongo 集合 `watchlists`（与真实持仓平行） |
| 收藏页内容 | 带行情：名称/代码、现价、涨跌幅 + 取消收藏 / 诊断 / K 线 |
| Agent 写操作 | 直接生效，**不需要** `confirm=true` |
| 上限 | 每用户最多 100 只 |
| 备注/标签 | 不做（本版非目标） |
| UI 控件 | 空心星 = 未收藏，实心星 = 已收藏 |

## 非目标

- 收藏分组、排序拖拽、备注、价格提醒
- 与真实持仓合并展示
- 收藏自动下单 / 一键买入

## 架构

```text
前端
  ├─ 今日关注 / 标的诊断  ──星标──►  POST/DELETE /watchlist/{symbol}
  ├─ 我的收藏页           ──列表──►  GET /watchlist + GET /watchlist/marks
  └─ 批量星态（今日关注） ────────►  GET /watchlist/status?symbols=

后端 watchlist.py  （镜像 portfolio.py）
  └─ Mongo db.watchlists  { user_id, items[], updated_at }

Agent tools.py
  ├─ get_watchlist
  ├─ add_watchlist_symbol
  └─ remove_watchlist_symbol
```

行情复用现有 `get_last_quote` / `trading_session`；刷新策略对齐持仓一览：交易时段约 3 秒，非交易时段进入页面刷新一次。

## 数据模型

集合：`watchlists`  
索引：`user_id` 唯一（在 `db.py` 与 portfolios 一并创建）。

```text
{
  user_id: str,
  items: [
    {
      symbol: str,      # 6 位，normalize_symbol
      name: str,        # 保存/回填时自动取名；缺省可用代码
      added_at: datetime
    }
  ],
  updated_at: datetime
}
```

规则：

- 同一 `symbol` 只保留一条；重复收藏视为幂等成功。
- 取消不存在的代码视为幂等成功。
- 达到 100 只后再新增 → HTTP 400 / 工具返回明确错误。
- 名称解析优先行情；失败则保留已有名称或回退为代码（同持仓 marks 名称逻辑）。

## API

前缀：`/api/advisor`，均需登录，`Depends(_user)` + `user["id"]`。

| Method | Path | 说明 |
|--------|------|------|
| GET | `/watchlist` | `{ items: [{ symbol, name, added_at }] }` |
| GET | `/watchlist/marks` | 带行情快照（见下） |
| GET | `/watchlist/status?symbols=a,b,c` | `{ starred: { "510300": true, ... } }`；未知代码为 false |
| POST | `/watchlist/{symbol}` | 收藏；可选 query/body `name`；返回更新后列表 |
| DELETE | `/watchlist/{symbol}` | 取消；返回更新后列表 |

### `GET /watchlist/marks` 响应形状

```text
{
  session: { is_trading, now, ... },   # trading_session()
  updated_at: str,
  count: int,
  items: [
    {
      symbol, name, added_at,
      price, pre_close, day_chg_pct,
      error?
    }
  ]
}
```

不计算市值/仓位（收藏无数量）。

## 前端

### 导航与路由

- `App.tsx` 基础面板 nav：「我的持仓」旁增加「我的收藏」→ `/watchlist`
- 新页：`WatchlistPage.tsx`

### 星标组件

- 共用小组件（如 `StarToggle`）：按钮，`aria-pressed` / `aria-label` 区分收藏与取消
- 空心 / 实心星（SVG 或 Unicode），使用品牌色；点击乐观更新 + 失败回滚

### 挂点

1. **今日关注**：`RecommendationCard` footer + 桌面 `BoardTable` `row-actions`；进入列表时用 `/watchlist/status` 批量拉星态
2. **标的诊断**：`AdviceCard` 头部或 actions 区；按当前 `symbol` 查是否已收藏
3. **我的收藏**：表格列：名称/代码、现价/涨跌幅、操作（星取消、诊断、查看K线）；meta 行显示更新时间与交易时段提示

### API 封装

`frontend-advisor/src/api.ts`：`fetchWatchlist`、`fetchWatchlistMarks`、`fetchWatchlistStatus`、`addWatchlist`、`removeWatchlist`。

## Agent 工具

在 `build_tools(user_id)` 注册（**无需 confirm**）：

| Tool | 行为 |
|------|------|
| `get_watchlist` | 返回收藏列表；可附带轻量现价/涨跌（调用 marks 或逐只 quote） |
| `add_watchlist_symbol` | `symbol` 必填；超限返回错误文案 |
| `remove_watchlist_symbol` | `symbol` 必填；幂等 |

系统提示补充：

- 用户说「收藏 / 加自选 / 取消收藏」时调用上述工具，**先工具成功再口头确认**
- 与真实持仓工具区分：收藏 ≠ 持仓，勿写入 `portfolios`

## 测试

- 后端：增删幂等、上限 100、normalize、marks 字段
- 前端：`StarToggle` / 卡片星标交互；收藏页渲染
- Agent：工具绑定 `user_id` 后读写正确集合（可单测 mock Mongo）

## 实现顺序建议

1. `watchlist.py` + 索引 + 路由 + 单测  
2. 前端 API + `StarToggle` + 今日关注 / 诊断挂点  
3. `WatchlistPage` + 导航  
4. Agent 工具 + 系统提示  
5. 构建验证
