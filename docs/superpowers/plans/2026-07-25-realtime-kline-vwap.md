# 实时 K 线均价线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在实时 K 线图上绘制准确的成交量加权均价（VWAP）线；后端透传/计算 `avg_price`，前端仅在 realtime 范围展示。

**Architecture:** 东方财富 trends 行第 8 列均价直接写入 bar；BaoStock 分钟线用当日累计成交额/累计成交量计算；前端在 realtime 下增加黄色「均价」折线，并在悬停区显示。逐笔回退路径不伪造均价。

**Tech Stack:** Python（pytest）、React + lightweight-charts、TypeScript；前端新增 vitest 测纯函数。

## Global Constraints

- 仅 `range === "realtime"` 展示均价线；不改 5 日/日/周/月图行为。
- `avg_price` 为截至该点的当日累计 VWAP，可选字段。
- 无效/非正/非有限均价不写入、不绘制；主价格线仍正常。
- 前端不使用 OHLC 近似计算 VWAP。
- 颜色暖黄，标题「均价」。

---

## File Structure

| 文件 | 职责 |
|------|------|
| `backend/app/kline.py` | trends 解析 `avg_price`；`_bar` 支持字段 |
| `backend/app/providers/baostock_kline.py` | 分钟 bar 按日累计 VWAP |
| `backend/tests/test_kline_avg_price.py` | 后端均价解析/累计测试 |
| `frontend/src/klineApi.ts` | 类型 + 有效均价/是否展示辅助函数 |
| `frontend/src/klineApi.test.ts` | 前端纯函数测试 |
| `frontend/src/components/KlineChart.tsx` | 绘制均价线 + hover 字段 |
| `frontend/src/KlinePage.tsx` | 悬停/图例显示均价 |
| `frontend/src/styles.css` | 均价文字颜色 |
| `frontend/package.json` / `vitest.config.ts` | 最小 vitest 配置 |

---

### Task 1: 后端东方财富 trends 解析 avg_price

**Files:**
- Modify: `backend/app/kline.py`
- Create: `backend/tests/test_kline_avg_price.py`

**Interfaces:**
- Consumes: trends 行字符串 `时间,开,收,高,低,成交量,成交额,均价`
- Produces: `_bar(..., avg_price=...)`；`_safe_avg_price(raw) -> float | None`；bar 可选键 `avg_price: float`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_kline_avg_price.py`：

```python
from app.kline import _bar, _safe_avg_price, _parse_trend_row


def test_safe_avg_price_accepts_positive():
    assert _safe_avg_price("12.34") == 12.34
    assert _safe_avg_price(12.34) == 12.34


def test_safe_avg_price_rejects_invalid():
    assert _safe_avg_price(None) is None
    assert _safe_avg_price("") is None
    assert _safe_avg_price("0") is None
    assert _safe_avg_price("-1") is None
    assert _safe_avg_price("nan") is None
    assert _safe_avg_price("inf") is None


def test_bar_includes_avg_price_when_valid():
    item = _bar("2026-07-25 09:31", 10, 11, 9, 10.5, 1000, avg_price=10.2)
    assert item["avg_price"] == 10.2


def test_bar_omits_invalid_avg_price():
    item = _bar("2026-07-25 09:31", 10, 11, 9, 10.5, 1000, avg_price=0)
    assert "avg_price" not in item


def test_parse_trend_row_reads_eighth_column():
    # 时间,开,收,高,低,成交量,成交额,均价
    row = "2026-07-25 09:31,10.0,10.5,10.8,9.9,1000,10500,10.5"
    bar = _parse_trend_row(row)
    assert bar is not None
    assert bar["close"] == 10.5
    assert bar["volume"] == 1000.0
    assert bar["avg_price"] == 10.5


def test_parse_trend_row_without_avg_still_works():
    row = "2026-07-25 09:31,10.0,10.5,10.8,9.9,1000"
    bar = _parse_trend_row(row)
    assert bar is not None
    assert "avg_price" not in bar
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_kline_avg_price.py -v
```

Expected: FAIL（`_safe_avg_price` / `_parse_trend_row` 未定义，或 `_bar` 不接受 `avg_price`）

- [ ] **Step 3: 实现最小代码**

在 `backend/app/kline.py`：

1. 增加：

```python
import math

def _safe_avg_price(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value
```

2. 扩展 `_bar`：

```python
def _bar(
    time: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None = None,
    avg_price: float | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "time": time,
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }
    if volume is not None and not pd.isna(volume):
        item["volume"] = float(volume)
    safe_avg = _safe_avg_price(avg_price)
    if safe_avg is not None:
        item["avg_price"] = safe_avg
    return item
```

3. 抽出 `_parse_trend_row`，并让 `_fetch_trends` 使用它：

```python
def _parse_trend_row(row: Any) -> dict[str, Any] | None:
    parts = str(row).split(",")
    if len(parts) < 6:
        return None
    avg = _safe_avg_price(parts[7]) if len(parts) >= 8 else None
    return _bar(
        parts[0],
        float(parts[1]),
        float(parts[3]),
        float(parts[4]),
        float(parts[2]),
        float(parts[5]),
        avg_price=avg,
    )
```

`_fetch_trends` 循环改为：

```python
for row in data.get("trends") or []:
    bar = _parse_trend_row(row)
    if bar is not None:
        bars.append(bar)
```

**不要**改逐笔回退路径（仍不写 `avg_price`）。

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && python -m pytest tests/test_kline_avg_price.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/kline.py backend/tests/test_kline_avg_price.py docs/superpowers/specs/2026-07-25-realtime-kline-vwap-design.md
git commit -m "$(cat <<'EOF'
feat: parse eastmoney trend avg_price into kline bars

EOF
)"
```

---

### Task 2: BaoStock 分钟线按日累计 VWAP

**Files:**
- Modify: `backend/app/providers/baostock_kline.py`
- Modify: `backend/tests/test_kline_avg_price.py`

**Interfaces:**
- Consumes: DataFrame 列 `date,time,open,high,low,close,volume,amount`
- Produces: `_bars_from_min_df(df) -> list[dict]`，每 bar 可选 `avg_price = 当日累计 amount / 当日累计 volume`

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_kline_avg_price.py`：

```python
import pandas as pd
from app.providers.baostock_kline import _bars_from_min_df


def test_baostock_min_bars_cumulative_vwap_resets_by_day():
    df = pd.DataFrame(
        [
            {
                "date": "2026-07-24",
                "time": "20260724093500000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
                "amount": 1000,
            },
            {
                "date": "2026-07-24",
                "time": "20260724094000000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 12,
                "volume": 100,
                "amount": 1400,
            },
            {
                "date": "2026-07-25",
                "time": "20260725093500000",
                "open": 12,
                "high": 13,
                "low": 11,
                "close": 12,
                "volume": 50,
                "amount": 600,
            },
        ]
    )
    bars = _bars_from_min_df(df)
    assert bars[0]["avg_price"] == 10.0          # 1000/100
    assert bars[1]["avg_price"] == 12.0          # (1000+1400)/(100+100)
    assert bars[2]["avg_price"] == 12.0          # new day 600/50


def test_baostock_min_bars_carry_forward_on_zero_volume():
    df = pd.DataFrame(
        [
            {
                "date": "2026-07-25",
                "time": "20260725093500000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
                "amount": 1050,
            },
            {
                "date": "2026-07-25",
                "time": "20260725094000000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.2,
                "volume": 0,
                "amount": 0,
            },
        ]
    )
    bars = _bars_from_min_df(df)
    assert bars[0]["avg_price"] == 10.5
    assert bars[1]["avg_price"] == 10.5
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_kline_avg_price.py::test_baostock_min_bars_cumulative_vwap_resets_by_day tests/test_kline_avg_price.py::test_baostock_min_bars_carry_forward_on_zero_volume -v
```

Expected: FAIL（当前 `_bars_from_min_df` 无 `avg_price`）

- [ ] **Step 3: 实现 `_bars_from_min_df` 累计逻辑**

在 `backend/app/providers/baostock_kline.py`：

1. `import math`
2. 扩展本地 `_bar` 同 Task 1（支持 `avg_price`，用相同校验：有限且 `> 0`）
3. 重写 `_bars_from_min_df`：

```python
def _bars_from_min_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    current_day: str | None = None
    cum_amount = 0.0
    cum_volume = 0.0
    last_avg: float | None = None
    for _, row in df.iterrows():
        try:
            date = str(row["date"])
            t = _parse_minute_time(date, row.get("time", ""))
            vol = _f(row["volume"]) if "volume" in row and str(row["volume"]) != "" else None
            amt = _f(row["amount"]) if "amount" in row and str(row["amount"]) != "" else None
            if date != current_day:
                current_day = date
                cum_amount = 0.0
                cum_volume = 0.0
                last_avg = None
            avg: float | None = None
            if (
                vol is not None
                and amt is not None
                and math.isfinite(vol)
                and math.isfinite(amt)
                and vol > 0
                and amt > 0
            ):
                cum_volume += vol
                cum_amount += amt
                if cum_volume > 0:
                    last_avg = cum_amount / cum_volume
                    avg = last_avg
            elif last_avg is not None:
                avg = last_avg
            bars.append(
                _bar(
                    t,
                    _f(row["open"]),
                    _f(row["high"]),
                    _f(row["low"]),
                    _f(row["close"]),
                    vol,
                    avg_price=avg,
                )
            )
        except (TypeError, ValueError):
            continue
    return bars
```

- [ ] **Step 4: 运行全部均价测试**

```bash
cd backend && python -m pytest tests/test_kline_avg_price.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/providers/baostock_kline.py backend/tests/test_kline_avg_price.py
git commit -m "$(cat <<'EOF'
feat: compute baostock intraday cumulative VWAP

EOF
)"
```

---

### Task 3: 前端类型、均价线与悬停展示

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Modify: `frontend/src/klineApi.ts`
- Create: `frontend/src/klineApi.test.ts`
- Modify: `frontend/src/components/KlineChart.tsx`
- Modify: `frontend/src/KlinePage.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `KlineBar.avg_price?: number`
- Produces:
  - `isValidAvgPrice(v: unknown): v is number`
  - `shouldShowAvgPrice(range: KlineRange, bars: KlineBar[]): boolean`
  - `AVG_PRICE_COLOR = '#f0c27a'`
  - `HoverBar.avgPrice?: number`
  - realtime 下第二条 `LineSeries`（title「均价」）

- [ ] **Step 1: 添加 vitest 并写失败测试**

`frontend/package.json` scripts 增加：

```json
"test": "vitest run"
```

devDependencies 增加：`"vitest": "^4.1.10"`（与 frontend-advisor 对齐即可）

创建 `frontend/vitest.config.ts`：

```ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
```

`frontend/src/klineApi.ts` 先只扩展类型（测试会因缺少函数失败）：

```ts
export type KlineBar = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
  avg_price?: number
}
```

创建 `frontend/src/klineApi.test.ts`：

```ts
import { describe, expect, it } from 'vitest'
import {
  isValidAvgPrice,
  shouldShowAvgPrice,
  type KlineBar,
} from './klineApi'

describe('avg price helpers', () => {
  it('accepts finite positive prices', () => {
    expect(isValidAvgPrice(10.5)).toBe(true)
    expect(isValidAvgPrice(0)).toBe(false)
    expect(isValidAvgPrice(-1)).toBe(false)
    expect(isValidAvgPrice(Number.NaN)).toBe(false)
    expect(isValidAvgPrice(undefined)).toBe(false)
  })

  it('shows avg only for realtime with valid points', () => {
    const bars: KlineBar[] = [
      { time: '2026-07-25 09:31', open: 1, high: 1, low: 1, close: 1, avg_price: 1.1 },
    ]
    expect(shouldShowAvgPrice('realtime', bars)).toBe(true)
    expect(shouldShowAvgPrice('daily', bars)).toBe(false)
    expect(shouldShowAvgPrice('realtime', [{ ...bars[0], avg_price: undefined }])).toBe(false)
  })
})
```

安装并跑测：

```bash
cd frontend && npm install && npm test
```

Expected: FAIL（`isValidAvgPrice` / `shouldShowAvgPrice` 未导出）

- [ ] **Step 2: 实现 klineApi 辅助函数**

```ts
export const AVG_PRICE_COLOR = '#f0c27a'

export function isValidAvgPrice(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

export function shouldShowAvgPrice(range: KlineRange, bars: KlineBar[]): boolean {
  return range === 'realtime' && bars.some((b) => isValidAvgPrice(b.avg_price))
}
```

再跑：

```bash
cd frontend && npm test
```

Expected: PASS

- [ ] **Step 3: KlineChart 绘制均价线并写入 hover**

1. Import `AVG_PRICE_COLOR`, `isValidAvgPrice`, `shouldShowAvgPrice`
2. `HoverBar` 增加 `avgPrice?: number`
3. 在构建 `lookup` 时：

```ts
avgPrice: isValidAvgPrice(b.avg_price) ? b.avg_price : undefined,
```

4. 在 `isLine` 分支、价格线 `setData` / 昨收线之后增加：

```ts
if (shouldShowAvgPrice(data.range, bars)) {
  const avgPoints: LineData[] = []
  for (const b of bars) {
    if (!isValidAvgPrice(b.avg_price)) continue
    avgPoints.push({
      time: toChartTime(b.time, useUnix),
      value: b.avg_price,
    })
  }
  if (avgPoints.length > 0) {
    const avgSeries = chart.addSeries(LineSeries, {
      color: AVG_PRICE_COLOR,
      lineWidth: 1,
      lastValueVisible: true,
      priceLineVisible: false,
      priceScaleId: 'left',
      crosshairMarkerVisible: false,
      title: '均价',
      priceFormat,
    })
    seriesRef.current.push(avgSeries)
    avgSeries.setData(avgPoints)
  }
}
```

确保该块**只**在 realtime 显示（由 `shouldShowAvgPrice` 保证），日 K 的 MA 逻辑不变。

- [ ] **Step 4: KlinePage 悬停展示 + 图例**

在 OHLC 行、量之后增加：

```tsx
{range === 'realtime' && active.avgPrice != null ? (
  <span className="kline-ma kline-avg">
    均价 {formatPrice(active.avgPrice, symbol)}
  </span>
) : null}
```

在日 K 图例旁或 realtime 时增加：

```tsx
{range === 'realtime' ? (
  <p className="kline-ma-legend" aria-hidden>
    <span className="kline-ma kline-avg">均价</span>
  </p>
) : null}
```

无 hover 时 `data.last` 若含 `avg_price`，需映射到 `avgPrice`。在 `active` 的 `useMemo` 中：

```ts
const active = useMemo(() => {
  const base = hover ?? data?.last ?? null
  if (!base) return null
  if (hover) return hover
  const last = base as typeof base & { avg_price?: number }
  const withAvg =
    range === 'realtime' && typeof last.avg_price === 'number'
      ? { ...last, avgPrice: last.avg_price }
      : last
  if (range !== 'daily' || !data?.bars?.length) return withAvg
  const i = data.bars.length - 1
  return {
    ...withAvg,
    ma5: computeSma(data.bars, 5)[i],
    ma10: computeSma(data.bars, 10)[i],
    ma20: computeSma(data.bars, 20)[i],
  }
}, [hover, data, range])
```

`styles.css` 增加：

```css
.kline-avg {
  color: #f0c27a;
}
```

- [ ] **Step 5: 验证**

```bash
cd frontend && npm test && npm run build
cd ../backend && python -m pytest tests/test_kline_avg_price.py -v
```

Expected: 全部 PASS；前端 build exit 0

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts \
  frontend/src/klineApi.ts frontend/src/klineApi.test.ts \
  frontend/src/components/KlineChart.tsx frontend/src/KlinePage.tsx frontend/src/styles.css
git commit -m "$(cat <<'EOF'
feat: draw realtime VWAP avg-price line on kline chart

EOF
)"
```

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|-----------|------|
| Eastmoney 第 8 列均价 | Task 1 |
| BaoStock 累计成交额/量 | Task 2 |
| 逐笔回退不伪造均价 | Task 1（明确不改） |
| 仅 realtime 展示黄线「均价」 | Task 3 |
| 悬停显示均价 | Task 3 |
| 缺失时主线正常 | Task 1–3 过滤逻辑 |
| 不改其他 range 行为 | Task 3 `shouldShowAvgPrice` |
| 后端/前端测试 | Task 1–3 |
