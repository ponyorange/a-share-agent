# Agent 主要指数行情感知

日期：2026-07-21  
状态：已确认设计

## 问题

投研助手（advisor agent）无法回答「科创50多少点」等大盘问题：现有工具只有资讯/情绪类，没有指数实时报价。前端 `/akshare/market` 与后端 `GET /api/akshare/market` 已具备同源数据（含科创50 `000688`）。

## 目标

用户按需询问主要指数点位或大盘概况时，agent 调用工具获取与大盘页一致的实时数据，禁止编造点位。

## 非目标

- 不改大盘页前端
- 不每轮对话自动注入行情（避免固定 token 开销）
- 不加个股实时报价
- 不新增独立 HTTP API
- 不返回涨跌榜 / 成交额榜

## 方案

新增只读 LangChain 工具 `fetch_market_indices`，复用 `app.market.get_market()`，裁剪为 featured 主要指数快照。

### 数据源

- 模块：`backend/app/market.py` → `get_market()`
- 与大盘页同源（东方财富 ulist，失败时新浪 fallback）
- Featured 指数（含科创50）：上证指数、深证成指、创业板指、科创50、沪深300、中证500、中证1000、上证50、北证50

### 工具契约

- 名称：`fetch_market_indices`
- 参数：无
- 返回：JSON 字符串，形状：

```json
{
  "updated_at": "<ISO>",
  "source": "eastmoney.ulist | sina.index_spot | ...",
  "indices": [
    {
      "symbol": "000688",
      "name": "科创50",
      "price": 1234.56,
      "change": 12.34,
      "change_pct": 1.01,
      "amount": 123456789.0
    }
  ]
}
```

- 实现：调用 `get_market()`，取 `featured`（或等价过滤），映射为上述精简字段；忽略 `boards` / `summary` / 未 featured 的 `indices`
- 异常：捕获后返回 `{"error": "<message>", "indices": []}`，agent 如实告知行情暂不可用

### 注册与 Prompt

1. 在 `backend/app/advisor/agent/tools.py` 的 `build_tools` 内定义 `@tool`，并加入 return 列表
2. 在 `backend/app/advisor/agent/graph.py` 的 `SYSTEM_PROMPT` 增加规则：询问指数点位、涨跌、大盘概况时必须先调用 `fetch_market_indices`，不得编造点位

## 验收

1. 问「科创50多少点」→ agent 调用 `fetch_market_indices` → 回答含点位与涨跌幅
2. 工具返回的科创50数据与同期 `/api/akshare/market` 的 featured 项一致（允许因抓取时点略有延迟）
3. 行情源失败时不编造数字，明确说明暂不可用

## 实现触及文件

- `backend/app/advisor/agent/tools.py`（新增工具 + 注册）
- `backend/app/advisor/agent/graph.py`（SYSTEM_PROMPT 一行指引）
