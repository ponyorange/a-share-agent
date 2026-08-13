import { useCallback, useEffect, useState } from 'react'
import {
  fetchGraphSignal,
  fetchSignalGraphPending,
  fetchSignalGraphSettled,
  fetchSignalGraphSummary,
  fetchSignalGraphView,
  postGraphSignals,
  postSignalGraphSettle,
  postSignalGraphSynthetic,
  type GraphSignalItem,
  type GraphViewPayload,
  type SignalGraphSummary,
} from '../api'
import { SignalGraphView } from '../components/SignalGraphView'
import {
  formatGraphAction,
  highlightFromEvidence,
  highlightFromNode,
  type HighlightState,
} from '../signalGraphLayout'

function fmtScore(scores: Record<string, number> | undefined): string {
  if (!scores) return '—'
  return `买 ${Number(scores.BUY || 0).toFixed(2)} / 持 ${Number(scores.HOLD || 0).toFixed(2)} / 卖 ${Number(
    scores.SELL || 0,
  ).toFixed(2)}`
}

function actionClass(action: string | undefined): string {
  if (action === 'BUY') return 'action-badge action-buy'
  if (action === 'SELL') return 'action-badge action-sell'
  if (action === 'HOLD') return 'action-badge action-hold'
  return 'action-badge'
}

function asText(value: unknown, fallback = '—'): string {
  if (value == null || value === '') return fallback
  return String(value)
}

export default function SignalGraphPage() {
  const [summary, setSummary] = useState<SignalGraphSummary | null>(null)
  const [view, setView] = useState<GraphViewPayload | null>(null)
  const [viewError, setViewError] = useState<string | null>(null)
  const [highlight, setHighlight] = useState<HighlightState | null>(null)
  const [symbol, setSymbol] = useState('600519')
  const [symbolsText, setSymbolsText] = useState('600519\n000001\n300750')
  const [one, setOne] = useState<GraphSignalItem | null>(null)
  const [batch, setBatch] = useState<GraphSignalItem[]>([])
  const [pending, setPending] = useState<Record<string, unknown>[]>([])
  const [settled, setSettled] = useState<Record<string, unknown>[]>([])
  const [synthetic, setSynthetic] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshMeta = useCallback(async (includeView = true) => {
    const viewReq = includeView
      ? fetchSignalGraphView().catch((err) => {
          setViewError(err instanceof Error ? err.message : String(err))
          return null
        })
      : Promise.resolve(null)
    const [s, p, t, v] = await Promise.all([
      fetchSignalGraphSummary(),
      fetchSignalGraphPending(30),
      fetchSignalGraphSettled(30),
      viewReq,
    ])
    setSummary(s)
    setPending(p.items || [])
    setSettled(t.items || [])
    if (v) {
      setViewError(null)
      setView(v)
    }
  }, [])

  useEffect(() => {
    let alive = true
    refreshMeta()
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      alive = false
    }
  }, [refreshMeta])

  async function run(label: string, fn: () => Promise<void>, includeView = true) {
    setBusy(label)
    setError(null)
    try {
      await fn()
      await refreshMeta(includeView)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="page signal-graph-page">
      <div className="page-hero">
        <h1>图学习</h1>
        <p>左边是市场、行业、形态，右边是买入 / 持有 / 卖出。点节点看路径，生成信号会在图上高亮。</p>
      </div>

      {error ? <p className="status error">{error}</p> : null}

      <div className="stat-row">
        <div className="stat">
          <span className="metric-label">节点</span>
          <div className="metric-value">{summary ? summary.node_count ?? 0 : '…'}</div>
        </div>
        <div className="stat">
          <span className="metric-label">边</span>
          <div className="metric-value">{summary ? summary.edge_count ?? 0 : '…'}</div>
        </div>
        <div className="stat">
          <span className="metric-label">待结算</span>
          <div className="metric-value">{summary ? summary.pending_count ?? 0 : '…'}</div>
          <span className="meta-line">未决 {summary?.unresolved_count ?? 0}</span>
        </div>
        <div className="stat">
          <span className="metric-label">已结算</span>
          <div className="metric-value">{summary ? summary.settled_count ?? 0 : '…'}</div>
        </div>
        <div className="stat">
          <span className="metric-label">最近交易日</span>
          <div className="metric-value signal-graph-stat-text">
            {summary?.latest_trade_date || '—'}
          </div>
          <span className="meta-line">tick {summary?.latest_trade_tick ?? '—'}</span>
        </div>
        <div className="stat">
          <span className="metric-label">自进化</span>
          <div className="metric-value signal-graph-stat-text">
            {summary?.last_evolve_date || '尚未跑过'}
          </div>
          <span className="meta-line">
            {summary?.config?.auto_evolve === false ? '已关闭' : '收盘后自动跑'}
          </span>
        </div>
      </div>

      <section className="signal-graph-panel">
        <header className="signal-graph-panel-head">
          <h2>全图</h2>
        </header>
        <SignalGraphView
          payload={view}
          error={viewError}
          highlight={highlight}
          onSelectNode={(id) => {
            if (view) setHighlight(highlightFromNode(id, view.edges))
          }}
          onResetView={() => setHighlight(null)}
        />
      </section>

      <div className="signal-graph-work">
        <section className="signal-graph-panel">
          <header className="signal-graph-panel-head">
            <h2>查一只</h2>
          </header>
          <div className="row-actions">
            <input
              className="input"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="6 位代码"
              aria-label="股票代码"
            />
            <button
              type="button"
              className="btn"
              disabled={Boolean(busy)}
              onClick={() =>
                void run(
                  'one',
                  async () => {
                    const item = await fetchGraphSignal(symbol.trim())
                    setOne(item)
                    setHighlight(highlightFromEvidence(item.evidence || [], item.action))
                  },
                  false,
                )
              }
            >
              {busy === 'one' ? '生成中…' : '生成信号'}
            </button>
          </div>
          {one ? (
            <div className="signal-graph-result">
              <div className="signal-graph-result-title">
                <strong>
                  {one.symbol} {one.name}
                </strong>
                <span className={actionClass(one.action)}>{formatGraphAction(one.action)}</span>
              </div>
              <p>{fmtScore(one.scores)}</p>
              <p className="muted">
                市场 {one.market_regime || '—'}
                {(one.patterns || []).length ? ` · ${(one.patterns || []).join('、')}` : ''}
              </p>
              {one.blocked_reason ? <p className="status error">{one.blocked_reason}</p> : null}
            </div>
          ) : (
            <p className="muted">生成后会在上图高亮用到的边。</p>
          )}
        </section>

        <section className="signal-graph-panel">
          <header className="signal-graph-panel-head">
            <h2>跑批 / 结算</h2>
          </header>
          <label className="signal-graph-field">
            <span>代码，空格或换行分隔</span>
            <textarea
              className="input"
              rows={3}
              value={symbolsText}
              onChange={(e) => setSymbolsText(e.target.value)}
              aria-label="批量代码"
            />
          </label>
          <div className="row-actions">
            <button
              type="button"
              className="btn"
              disabled={Boolean(busy)}
              onClick={() =>
                run('batch', async () => {
                  const symbols = symbolsText
                    .split(/[\s,，]+/)
                    .map((s) => s.trim())
                    .filter(Boolean)
                  const res = await postGraphSignals({ symbols, persist: true })
                  setBatch(res.items || [])
                })
              }
            >
              {busy === 'batch' ? '跑批中…' : '跑批并写入'}
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={Boolean(busy)}
              onClick={() =>
                run('settle', async () => {
                  await postSignalGraphSettle({})
                })
              }
            >
              {busy === 'settle' ? '结算中…' : '结算到期'}
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={Boolean(busy)}
              onClick={() =>
                run('synthetic', async () => {
                  setSynthetic(await postSignalGraphSynthetic({ seed: 7, days: 60 }))
                })
              }
            >
              {busy === 'synthetic' ? '回测中…' : '合成演示'}
            </button>
          </div>
          {synthetic ? (
            <p className="muted">
              合成演示：信号 {asText(synthetic.signal_count)} · 结算 {asText(synthetic.settled_count)} ·
              正反馈 {asText(synthetic.positive_feedback)} · 边 {asText(synthetic.edge_count)}
            </p>
          ) : null}
          {batch.length ? (
            <div className="signal-graph-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>动作</th>
                    <th>分数</th>
                  </tr>
                </thead>
                <tbody>
                  {batch.map((row) => (
                    <tr key={`${row.symbol}-${row.prediction_id || row.action}`}>
                      <td>
                        {row.symbol} {row.name}
                      </td>
                      <td>
                        <span className={actionClass(row.action)}>{formatGraphAction(row.action)}</span>
                      </td>
                      <td>{fmtScore(row.scores)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      </div>

      <section className="signal-graph-panel">
        <header className="signal-graph-panel-head">
          <h2>结算记录</h2>
        </header>
        <div className="signal-graph-ledger">
          <div>
            <h3>待结算 {pending.length}</h3>
            {pending.length ? (
              <div className="signal-graph-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th>动作</th>
                      <th>到期 tick</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pending.slice(0, 12).map((row) => (
                      <tr key={asText(row.prediction_id)}>
                        <td>{asText(row.ticker)}</td>
                        <td>{formatGraphAction(asText(row.action, ''))}</td>
                        <td>{asText(row.due_tick)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">暂无</p>
            )}
          </div>
          <div>
            <h3>已结算 {settled.length}</h3>
            {settled.length ? (
              <div className="signal-graph-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th>动作</th>
                      <th>反馈</th>
                    </tr>
                  </thead>
                  <tbody>
                    {settled.slice(0, 12).map((row) => (
                      <tr key={asText(row.prediction_id)}>
                        <td>{asText(row.ticker)}</td>
                        <td>{formatGraphAction(asText(row.action, ''))}</td>
                        <td>{asText(row.feedback_delta)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">暂无</p>
            )}
          </div>
        </div>
      </section>
    </section>
  )
}
