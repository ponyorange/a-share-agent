# Paper Trader Cockpit 三栏布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `/paper/trader` 改为三栏终端布局（左候选 | 中日 K+决策 | 右持仓+今日统计），并修复日 K 选中后空白。

**Architecture:** 仅改前端：`PaperTraderPage` DOM 拆成三栏舱；CSS 限高滚动；`PaperTraderChart` 始终挂载 host 再灌数据。API/轮询/启停不变。

**Tech Stack:** React、Vitest、Testing Library、lightweight-charts、现有 `styles.css` 变量

## Global Constraints

- Spec：`docs/superpowers/specs/2026-08-12-paper-trader-cockpit-layout-design.md`
- 桌面三栏约 26% / 48% / 26%；&lt;1100px 单列：候选 → K/决策 → 持仓
- 不改后端 cockpit API、worker、启停语义
- Docker 镜像标签仍为 `名称:架构`
- 计划中的 commit 步骤默认执行（小步提交）

---

### File map

| 文件 | 职责 |
|------|------|
| `frontend-advisor/src/components/PaperTraderChart.tsx` | 始终挂载 host；修初始化 |
| `frontend-advisor/src/pages/PaperTraderPage.tsx` | 三栏 DOM |
| `frontend-advisor/src/styles.css` | 三栏栅格、面板、滚动 |
| `frontend-advisor/src/pages/PaperTraderPage.test.tsx` | 三栏结构 + 启停回归 |

---

### Task 1: 修复 PaperTraderChart 初始化

**Files:**
- Modify: `frontend-advisor/src/components/PaperTraderChart.tsx`
- Modify: `frontend-advisor/src/pages/PaperTraderPage.test.tsx`（可选：组件级不测图表库，靠页面选中后 `chart:SYMBOL`）

**Interfaces:**
- Consumes: `fetchAdvisorKline(symbol, 'daily')`
- Produces: `PaperTraderChart({ symbol: string | null })` — host 始终在 DOM；无 symbol 显示占位，不销毁 chart

- [ ] **Step 1: 改组件 — 去掉无 symbol 时的提前 return，host 始终渲染**

将 `PaperTraderChart.tsx` 整文件替换为：

```tsx
import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
} from 'lightweight-charts'
import { fetchAdvisorKline } from '../api'

export default function PaperTraderChart({ symbol }: { symbol: string | null }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    const chart = createChart(el, {
      height: 300,
      layout: {
        background: { color: 'transparent' },
        textColor: '#9aa4b2',
      },
      grid: {
        vertLines: { color: 'rgba(127,127,127,0.15)' },
        horzLines: { color: 'rgba(127,127,127,0.15)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#d64545',
      downColor: '#1a7f37',
      borderVisible: false,
      wickUpColor: '#d64545',
      wickDownColor: '#1a7f37',
    })
    chartRef.current = chart
    seriesRef.current = series
    const onResize = () => {
      if (hostRef.current) {
        chart.applyOptions({ width: hostRef.current.clientWidth })
      }
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!seriesRef.current) return
    if (!symbol) {
      seriesRef.current.setData([])
      setError(null)
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchAdvisorKline(symbol, 'daily')
      .then((payload) => {
        if (cancelled) return
        const bars = Array.isArray(payload.bars) ? payload.bars : []
        const data = bars
          .map((b) => {
            const time = String(b.time || '').slice(0, 10)
            const open = Number(b.open)
            const high = Number(b.high)
            const low = Number(b.low)
            const close = Number(b.close)
            if (!time || [open, high, low, close].some((x) => Number.isNaN(x))) {
              return null
            }
            return { time, open, high, low, close }
          })
          .filter(Boolean) as Array<{
          time: string
          open: number
          high: number
          low: number
          close: number
        }>
        seriesRef.current?.setData(data as never)
        chartRef.current?.timeScale().fitContent()
        if (!data.length) setError('无 K 线数据')
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [symbol])

  return (
    <div className="paper-trader-chart">
      {!symbol ? <p className="status">选择标的查看日 K</p> : null}
      {symbol && loading ? <p className="status">K 线加载中…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      <div ref={hostRef} className="paper-trader-chart-host" />
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend-advisor/src/components/PaperTraderChart.tsx
git commit -m "fix: keep paper trader chart host mounted for init"
```

---

### Task 2: 三栏 DOM + CSS

**Files:**
- Modify: `frontend-advisor/src/pages/PaperTraderPage.tsx`（`.paper-trader-grid` 内）
- Modify: `frontend-advisor/src/styles.css`（`/* —— 交易员驾驶舱 —— */` 段）

**Interfaces:**
- Consumes: 现有 `candidates` / `positions` / `decisions` / `selectedSymbol` / `PaperTraderChart`
- Produces: DOM 结构
  - `.paper-trader-grid` → 三子节点 `.paper-trader-panel`：`cabin-candidates` / `cabin-center` / `cabin-side`
  - 右栏含「迷你持仓」+「今日统计」文案

- [ ] **Step 1: 改 `PaperTraderPage.tsx` 主舱为三栏**

将 `return` 中 `<div className="paper-trader-grid">...</div>` 整块替换为：

```tsx
      <div className="paper-trader-grid">
        <div className="paper-trader-panel cabin-candidates">
          <h3>候选池</h3>
          <div className="table-wrap paper-trader-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>方向</th>
                  <th>分数</th>
                  <th>图</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <tr
                    key={c.symbol}
                    className={selectedSymbol === c.symbol ? 'is-selected' : undefined}
                    onClick={() => setSelectedSymbol(c.symbol)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td>
                      {c.symbol} {c.name || ''}
                    </td>
                    <td className={`dir-${c.direction || 'neutral'}`}>{c.direction || '—'}</td>
                    <td>{c.rule_score != null ? Number(c.rule_score).toFixed(2) : '—'}</td>
                    <td>{c.graph_action || '—'}</td>
                  </tr>
                ))}
                {!candidates.length ? (
                  <tr>
                    <td colSpan={4}>
                      暂无候选（需「今日关注」有归档，或自选/模拟盘持仓）
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>

        <div className="paper-trader-panel cabin-center">
          <div className="paper-trader-chart-head">
            <h3>日 K {selectedSymbol ? `· ${selectedSymbol}` : ''}</h3>
            {selectedSymbol ? (
              <a href={explorerKlineUrl(selectedSymbol)} target="_blank" rel="noreferrer">
                详细 K 线
              </a>
            ) : null}
          </div>
          <PaperTraderChart symbol={selectedSymbol} />

          <h3>决策时间线</h3>
          <ul className="paper-trader-decisions paper-trader-scroll">
            {decisions.map((d) => {
              const id = String(d.id || d.run_id || '')
              const open = expandedDecision === id
              return (
                <li key={id || JSON.stringify(d)}>
                  <button
                    type="button"
                    className="btn linkish"
                    onClick={() => setExpandedDecision(open ? null : id)}
                  >
                    {String(d.finished_at || d.started_at || '—')} · skip=
                    {String(d.skip_reason || '—')} · 成交{' '}
                    {Array.isArray(d.orders_placed) ? d.orders_placed.length : 0} · 拦截{' '}
                    {Array.isArray(d.risk_blocked) ? d.risk_blocked.length : 0}
                  </button>
                  {open ? (
                    <pre className="paper-trader-decision-detail">
                      {JSON.stringify(
                        {
                          llm_actions: d.llm_actions,
                          risk_blocked: d.risk_blocked,
                          orders_placed: d.orders_placed,
                          skip_reason: d.skip_reason,
                          error: d.error,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  ) : null}
                </li>
              )
            })}
            {!decisions.length ? <li>尚无决策轮次</li> : null}
          </ul>
        </div>

        <div className="paper-trader-panel cabin-side">
          <h3>迷你持仓</h3>
          <ul className="paper-trader-positions paper-trader-scroll">
            {positions.map((p) => (
              <li key={String(p.symbol)}>
                <button
                  type="button"
                  className="btn linkish"
                  onClick={() => setSelectedSymbol(String(p.symbol))}
                >
                  {p.symbol} {p.name || ''} · qty {p.qty}
                </button>
              </li>
            ))}
            {!positions.length ? <li>无持仓</li> : null}
          </ul>
          {cockpit ? (
            <p className="muted paper-trader-stats">
              今日：轮次 {cockpit.session.stats_today?.rounds ?? 0} / 成交{' '}
              {cockpit.session.stats_today?.trades ?? 0} / 拦截{' '}
              {cockpit.session.stats_today?.blocked ?? 0}
            </p>
          ) : null}
        </div>
      </div>
```

保留顶栏、模式与风控、loading/error/message 不变。删除未使用的 `riskOpen` 以外的旧双栏结构即可（`riskOpen` 仍用于风控展开）。

- [ ] **Step 2: 替换 `styles.css` 中驾驶舱栅格相关规则**

在 `/* —— 交易员驾驶舱 —— */` 段，将 `.paper-trader-grid` 起到 `@media (max-width: 900px)` 块（含）替换为：

```css
.paper-trader-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.55fr) minmax(0, 0.85fr);
  gap: 0.75rem;
  align-items: stretch;
  min-height: min(70vh, 42rem);
}

.paper-trader-panel {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-width: 0;
  min-height: 0;
  padding: 0.65rem 0.75rem;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--bg2, color-mix(in srgb, var(--bg) 92%, #888 8%));
}

.paper-trader-panel > h3,
.paper-trader-chart-head {
  flex: 0 0 auto;
  margin: 0;
}

.paper-trader-scroll {
  flex: 1 1 auto;
  min-height: 0;
  max-height: min(52vh, 28rem);
  overflow: auto;
}

.cabin-center .paper-trader-scroll {
  max-height: min(28vh, 14rem);
}

.paper-trader-chart-host {
  width: 100%;
  height: 300px;
  min-height: 300px;
  flex: 0 0 auto;
}

.paper-trader-chart-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.5rem;
}

.paper-trader-positions,
.paper-trader-decisions {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.35rem;
  align-content: start;
}

.paper-trader-stats {
  margin-top: auto;
  padding-top: 0.35rem;
  border-top: 1px solid var(--line);
}

.paper-trader-decision-detail {
  margin: 0.35rem 0 0;
  padding: 0.5rem;
  overflow: auto;
  max-height: 12rem;
  font-size: 0.75rem;
  background: var(--bg, #fff);
  border: 1px solid var(--line);
  border-radius: 6px;
}

.paper-trader-page .dir-buy {
  color: var(--color-market-up, #d64545);
}

.paper-trader-page .dir-sell {
  color: var(--color-market-down, #1a7f37);
}

.paper-trader-page tr.is-selected td {
  background: color-mix(in srgb, var(--accent, #3b82f6) 12%, transparent);
}

@media (max-width: 1100px) {
  .paper-trader-grid {
    grid-template-columns: 1fr;
    min-height: 0;
  }

  .paper-trader-scroll {
    max-height: 18rem;
  }
}
```

保留同段上方的 `.paper-trader-page` / toolbar / config / badge 规则不动。删除旧的 `.paper-trader-col` 规则（若仍存在）。

- [ ] **Step 3: Commit**

```bash
git add frontend-advisor/src/pages/PaperTraderPage.tsx frontend-advisor/src/styles.css
git commit -m "feat: use three-column paper trader cockpit layout"
```

---

### Task 3: 测试与验收

**Files:**
- Modify: `frontend-advisor/src/pages/PaperTraderPage.test.tsx`

**Interfaces:**
- Consumes: mock `fetchPaperTraderCockpit` 返回候选；mock chart `data-testid="chart"`

- [ ] **Step 1: 扩展单测 — 三栏标题与选中 chart**

在现有 `it('loads cockpit...')` 后追加：

```tsx
it('renders three cabin panels and selects first candidate for chart', async () => {
  render(
    <MemoryRouter>
      <PaperTraderPage />
    </MemoryRouter>,
  )
  await waitFor(() => {
    expect(screen.getByText('候选池')).toBeInTheDocument()
  })
  expect(screen.getByText('迷你持仓')).toBeInTheDocument()
  expect(screen.getByText('决策时间线')).toBeInTheDocument()
  await waitFor(() => {
    expect(screen.getByTestId('chart')).toHaveTextContent('chart:600000')
  })
})
```

- [ ] **Step 2: 跑测试**

```bash
cd frontend-advisor && npm test -- --run src/pages/PaperTraderPage.test.tsx
```

Expected: PASS（含原启停用例 + 新三栏用例）

- [ ] **Step 3: Commit**

```bash
git add frontend-advisor/src/pages/PaperTraderPage.test.tsx
git commit -m "test: cover three-column paper trader cabin panels"
```

- [ ] **Step 4: 手动抽检**

硬刷新 `http://127.0.0.1:5174/paper/trader`：三栏对齐、候选可滚、选中后 K 线 host 非空白、窄窗单列。

---

## Spec coverage (self-review)

| Spec 项 | Task |
|---------|------|
| 三栏 26/48/26 | Task 2 CSS `0.85fr / 1.55fr / 0.85fr` |
| &lt;1100px 单列顺序 | Task 2 DOM 顺序 + media |
| 限高滚动 | Task 2 `.paper-trader-scroll` |
| K 线初始化修复 | Task 1 |
| API 不动 | 无后端任务 |
| vitest 启停仍过 | Task 3 |
