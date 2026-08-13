import { useCallback, useEffect, useState } from 'react'
import {
  fetchGraphSignal,
  fetchSignalGraphPending,
  fetchSignalGraphSettled,
  fetchSignalGraphSummary,
  postGraphSignals,
  postSignalGraphSettle,
  postSignalGraphSynthetic,
  type GraphSignalItem,
  type SignalGraphSummary,
} from '../api'

function fmtScore(scores: Record<string, number> | undefined): string {
  if (!scores) return '—'
  return `B ${Number(scores.BUY || 0).toFixed(2)} / H ${Number(scores.HOLD || 0).toFixed(2)} / S ${Number(
    scores.SELL || 0,
  ).toFixed(2)}`
}

export default function SignalGraphPage() {
  const [summary, setSummary] = useState<SignalGraphSummary | null>(null)
  const [symbol, setSymbol] = useState('600519')
  const [symbolsText, setSymbolsText] = useState('600519\n000001\n300750')
  const [one, setOne] = useState<GraphSignalItem | null>(null)
  const [batch, setBatch] = useState<GraphSignalItem[]>([])
  const [pending, setPending] = useState<Record<string, unknown>[]>([])
  const [settled, setSettled] = useState<Record<string, unknown>[]>([])
  const [synthetic, setSynthetic] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshMeta = useCallback(async () => {
    const [s, p, t] = await Promise.all([
      fetchSignalGraphSummary(),
      fetchSignalGraphPending(30),
      fetchSignalGraphSettled(30),
    ])
    setSummary(s)
    setPending(p.items || [])
    setSettled(t.items || [])
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

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label)
    setError(null)
    try {
      await fn()
      await refreshMeta()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <h1>图学习信号</h1>
        <p>共享 SignalGraph：生成日频 BUY/HOLD/SELL，到期按超额收益结算并更新边经验。</p>
      </div>

      {error ? <p className="status error">{error}</p> : null}

      <div className="diag-block">
        <h2>图状态</h2>
        {summary ? (
          <ul className="plain-list">
            <li>边 {summary.edge_count ?? 0} · 节点 {summary.node_count ?? 0}</li>
            <li>
              待结算 {summary.pending_count ?? 0} · 未决 {summary.unresolved_count ?? 0} · 已结算{' '}
              {summary.settled_count ?? 0}
            </li>
            <li>
              最近交易日 {summary.latest_trade_date || '—'} · tick {summary.latest_trade_tick ?? '—'}
            </li>
            <li>
              自进化 {summary.last_evolve_date || '尚未跑过'}
              {summary.config?.auto_evolve === false ? '（已关闭）' : ''}
            </li>
          </ul>
        ) : (
          <p className="status">加载中…</p>
        )}
      </div>

      <div className="diag-block">
        <h2>单票信号</h2>
        <div className="row-actions">
          <input
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
              run('one', async () => {
                setOne(await fetchGraphSignal(symbol.trim()))
              })
            }
          >
            {busy === 'one' ? '生成中…' : '生成'}
          </button>
        </div>
        {one ? (
          <div className="card-soft">
            <p>
              <strong>
                {one.symbol} {one.name}
              </strong>{' '}
              → {one.action}（raw {one.raw_action}）
            </p>
            <p>{fmtScore(one.scores)}</p>
            <p>
              regime {one.market_regime || '—'} · patterns {(one.patterns || []).join(', ') || '—'}
            </p>
            {one.prediction_id ? <p className="muted">prediction {one.prediction_id}</p> : null}
            {one.blocked_reason ? <p className="status">blocked: {one.blocked_reason}</p> : null}
          </div>
        ) : null}
      </div>

      <div className="diag-block">
        <h2>批量跑批</h2>
        <textarea
          rows={4}
          value={symbolsText}
          onChange={(e) => setSymbolsText(e.target.value)}
          aria-label="批量代码"
        />
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
            {busy === 'batch' ? '跑批中…' : '跑批并写入图'}
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
            {busy === 'settle' ? '结算中…' : '结算到期预测'}
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
            {busy === 'synthetic' ? '回测中…' : '合成回测演示'}
          </button>
        </div>
        {batch.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>动作</th>
                <th>分数</th>
                <th>预测 ID</th>
              </tr>
            </thead>
            <tbody>
              {batch.map((row) => (
                <tr key={`${row.symbol}-${row.prediction_id || row.action}`}>
                  <td>
                    {row.symbol} {row.name}
                  </td>
                  <td>{row.action}</td>
                  <td>{fmtScore(row.scores)}</td>
                  <td className="muted">{row.prediction_id || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {synthetic ? (
          <p className="muted">
            合成：信号 {String(synthetic.signal_count)} · 结算 {String(synthetic.settled_count)} ·
            正反馈 {String(synthetic.positive_feedback)} · 边 {String(synthetic.edge_count)}
          </p>
        ) : null}
      </div>

      <div className="diag-block">
        <h2>待结算 / 已结算</h2>
        <div className="two-col">
          <div>
            <h3>Pending ({pending.length})</h3>
            <ul className="plain-list">
              {pending.slice(0, 12).map((row) => (
                <li key={String(row.prediction_id)}>
                  {String(row.ticker)} {String(row.action)} due {String(row.due_tick)}
                </li>
              ))}
              {!pending.length ? <li className="muted">暂无</li> : null}
            </ul>
          </div>
          <div>
            <h3>Settled ({settled.length})</h3>
            <ul className="plain-list">
              {settled.slice(0, 12).map((row) => (
                <li key={String(row.prediction_id)}>
                  {String(row.ticker)} {String(row.action)} Δ{String(row.feedback_delta)}
                </li>
              ))}
              {!settled.length ? <li className="muted">暂无</li> : null}
            </ul>
          </div>
        </div>
      </div>
    </section>
  )
}
