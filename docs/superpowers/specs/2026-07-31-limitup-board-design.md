# 基础面板「打板」模块设计

## 目标

在数据后台（`frontend/`，5173）增加「打板」模块，展示：

1. **当天涨停**：交易时段内展示并约 10s 刷新；含当前封板与今日曾涨停后开板的标的，并打标记。
2. **连板看板**：按当前连板数从高到低分档（天梯）；盘中随接口刷新，收盘后仍展示当日最终结构（只读）。

本版只服务 **AKShare / A 股涨停池**，不扫 ETF，不做历史最高连板归档。

## 决策摘要

| 项 | 选择 |
|----|------|
| 连板看板语义 | 当日连板天梯（按当前连板数分档），非历史最高纪录 |
| 标的范围 | 东财 A 股涨停相关池；不做 ETF 全市场扫描 |
| 「今天涨停过」 | 涨停池（仍封板）+ 炸板池（今日曾涨停后开板） |
| 当天涨停可见性 | 仅 `is_trading` 时展示 + 10s 轮询 |
| 连板看板可见性 | 盘中 10s 刷新；盘后展示最近成功数据、不轮询 |
| 架构 | 薄封装专用 REST + 前端新 Tab（方案 A） |

## 架构

```text
PageNav「打板」 → /akshare/limitup
        │
        ▼
GET /api/akshare/limit-up
        │
        ├─ ak.stock_zt_pool_em      → today[].status=sealed
        ├─ ak.stock_zt_pool_zbgc_em → today[].status=broken
        └─ trading_session()       → session 字段
        │
        ▼
前端：表格「当天涨停」+ 「连板看板」；K 线深链到 /akshare/kline?symbol=&range=daily
```

短缓存（建议 5–8s）放在服务端，吸收 10s 轮询。

## API

### `GET /api/{source}/limit-up`

- 仅 `source=akshare` 实现；其它源 404。
- Provider `features` 增加 `limitup`。

**响应：**

```json
{
  "as_of": "2026-07-31T10:00:00+08:00",
  "session": {
    "is_trading": true,
    "is_trading_day": true
  },
  "today": [
    {
      "symbol": "000001",
      "name": "平安银行",
      "day_chg_pct": 0.10,
      "board_count": 1,
      "status": "sealed",
      "limit_up_price": null
    }
  ],
  "ladder": [
    {
      "board_count": 3,
      "items": [
        { "symbol": "300xxx", "name": "…", "day_chg_pct": 0.20 }
      ]
    }
  ]
}
```

**字段约定：**

- `day_chg_pct`：小数比例（`0.10` = 10%），与现有 quote/market 一致。
- `status`：`sealed`（当前涨停）| `broken`（今日曾涨停、现已开板）。同代码优先 `sealed`。
- `board_count`：取东财池「连板数」；缺失时按 `1`（仍在池内）或 `null` 再降级为 `1`。
- `ladder`：仅对 `status=sealed` 的标的按 `board_count` **降序**分组；空档省略；同档内可按涨幅或代码排序（实现选稳定序即可）。
- `limit_up_price`：有则填，无则 `null`。

**错误：** 上游失败返回 502 + 可读 detail；前端展示错误文案并允许重试。

## 前端

### 导航与路由

- `SourceFeature` 增加 `'limitup'`
- `PageNav` 增加「打板」链接（仅 `hasFeature(limitup)`）
- 路由：`/:source/limitup`；旧路径可选重定向到 `akshare`
- 页面组件：`LimitUpPage.tsx`（风格对齐 Market/Fund，不引入新设计体系）

### 「当天涨停」表

| 列 | 说明 |
|----|------|
| 标记 | 当前涨停 / 曾涨停 |
| 名称 | name |
| 代码 | symbol（mono） |
| 当日涨幅 | 格式化为百分比 |
| 连板 | board_count |
| 操作 | 「K线」→ `/${source}/kline?symbol=${symbol}&range=daily` |

- 仅当 `session.is_trading` 为真时渲染表格；否则简短占位（非交易时段不展示）。
- 盘中每 10s 请求一次；页面不可见时可暂停轮询（`document.visibilityState`）。

### 「连板看板」

- 每个 `ladder` 档位一块：标题「N 连板」，下列名称/代码/涨幅 + K 线链接。
- 盘中与「当天涨停」共用同一轮询；盘后：`is_trading=false` 时仍请求一次（或保留上次成功数据）并**停止** interval。

## 后端实现要点

- 新模块：`backend/app/limitup.py`（拉取、归一化、拼 ladder、短 TTL 缓存）。
- `AkshareProvider`：`features` 含 `limitup`；`get_limit_up()` 委托服务。
- `main.py`：注册 `GET /api/{source}/limit-up`。
- 日期参数：东财接口通常要交易日字符串；用 `trading_session` / 日历取「今日」上海日期；非交易日炸板/涨停池可能为空，属正常。

## 测试

- 后端：mock 两池 DataFrame → `today` 合并优先级、`ladder` 降序与分档。
- 前端：vitest 覆盖非交易隐藏「当天涨停」、交易时段展示标记文案（可浅测）。

## 非目标（本版）

- ETF 扫描
- 历史最高连板 / Mongo 归档 worker
- Tushare `limit_list_d` 双源
- 推送/告警

## 成功标准

1. akshare 顶栏出现「打板」，进入后可见两块区域。
2. 交易时段「当天涨停」约 10s 更新，且能区分当前涨停与曾涨停。
3. 「连板看板」按连板数从高到低分档；盘后仍可看当日最终天梯。
4. 行内可跳到对应标的日 K 线页。
