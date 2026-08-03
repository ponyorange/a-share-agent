# yfinance 数据源设计

## 目标

在数据后台接入 Yahoo Finance（`yfinance`）作为独立数据源，提供：

- **接口浏览器**（`explorer`）
- **大盘行情**（`market`，美股/全球指数为主）
- **K 线图**（`kline`，默认代码 `AAPL`）

不接入盘口 / 打板 / 基金，不改顾问侧。

## 决策

| 项 | 选择 |
|----|------|
| Provider id | `yfinance` |
| features | `explorer`, `market`, `kline` |
| 目录策略 | 宽目录手写 + 按族 `fetch` 分发（不做运行时反射） |
| 大盘内容 | 美股/全球指数；沪深成交额字段保持契约但可为 `null` |
| K 线 | `history` 映射 realtime/5d/daily/weekly/monthly；前端默认 `AAPL` |
| 依赖 | `requirements.txt` 增加 `yfinance`；出网访问 Yahoo |

## 架构

- `YfinanceProvider` 注册到 `providers` registry
- `yfinance_catalog.py`：分类目录与参数/示例
- `yfinance_market.py`：`get_market()` 对齐现有 `MarketResponse`
- 前端 `FALLBACK_SOURCES` 增加条目；大盘页在成交额全空时隐藏沪/深摘要，无 `kline` 时榜单不链到 K 线

## Explorer

分类：`ticker` / `download` / `search` / `screener` / `multi`。

接口名用稳定短名（如 `ticker_history`、`download`）。仅 catalog 内接口可调用；`Ticker` 族经 `yf.Ticker(symbol)` 取属性或调方法；结果经 `normalize_result`。

## Market

- `featured`：`^GSPC`、`^DJI`、`^IXIC`、`^RUT`、`^VIX`、`^FTSE`、`^N225`、`^HSI`
- `indices`：featured + 少量补充（如 `000001.SS`、`^GDAXI`、`BTC-USD`）
- `summary.amount_*`：`null`
- `boards`：gainers / losers / most actives；失败时空列表 + `error`
- `source`：`yfinance`

## 非目标

- K 线 / quote / limitup / fund
- 顾问 Agent 特化
- Docker 内 Yahoo 网络专项配置（失败由 health / market error 呈现）
