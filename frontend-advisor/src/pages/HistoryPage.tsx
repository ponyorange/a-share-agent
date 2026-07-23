import { useEffect, useRef, useState } from 'react'
import {
  fetchRecDates,
  fetchRecHistory,
  formatPct,
  formatScore,
  streamRecHistoryReturns,
  type AdviceItem,
  type HistoryStreamAccuracy,
} from '../api'
import { ActionBadge } from '../components/AdviceCard'

export default function HistoryPage() {
  const [dates, setDates] = useState<string[]>([])
  const [tradeDate, setTradeDate] = useState('')
  const [vsDate, setVsDate] = useState('')
  const [items, setItems] = useState<AdviceItem[]>([])
  const [accuracy, setAccuracy] = useState<HistoryStreamAccuracy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [computing, setComputing] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    fetchRecDates()
      .then((d) => {
        setDates(d.dates)
        if (d.dates[0]) setTradeDate(d.dates[0])
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!tradeDate) return
    abortRef.current?.abort()
    setComputing(false)
    setProgress(null)
    setAccuracy(null)
    setLoading(true)
    setError(null)
    fetchRecHistory(tradeDate)
      .then((res) => {
        setItems(
          (res.items || []).map((it) => ({
            ...it,
            return_pct: undefined,
            vs_close: undefined,
            base_close: it.close,
          })),
        )
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [tradeDate])

  async function computeReturns() {
    if (!tradeDate || computing) return
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setComputing(true)
    setError(null)
    setAccuracy(null)
    setProgress({ done: 0, total: items.length })
    // clear previous returns
    setItems((prev) =>
      prev.map((it) => ({
        ...it,
        return_pct: undefined,
        vs_close: undefined,
        base_close: it.close ?? it.base_close,
      })),
    )

    try {
      await streamRecHistoryReturns(
        tradeDate,
        vsDate || undefined,
        {
          onMeta: (meta) => setProgress({ done: 0, total: meta.total }),
          onItem: (row) => {
            setItems((prev) => {
              const next = [...prev]
              const idx = next.findIndex((x) => x.symbol === row.symbol)
              const patch: AdviceItem = {
                ...(idx >= 0 ? next[idx] : ({} as AdviceItem)),
                symbol: row.symbol,
                name: row.name || row.symbol,
                score: row.score ?? 0,
                action: (row.action as AdviceItem['action']) || 'watch',
                action_label: row.action_label || row.action || '',
                close: row.close,
                base_close: row.base_close,
                vs_close: row.vs_close,
                vs_date: row.vs_date,
                return_pct: row.return_pct,
                has_position: false,
                factors: [],
                rationale: '',
              }
              if (idx >= 0) next[idx] = patch
              else next.push(patch)
              return next
            })
            setProgress((p) =>
              p ? { ...p, done: Math.min(p.done + 1, p.total) } : { done: 1, total: 1 },
            )
          },
          onDone: (done) => setAccuracy(done.accuracy),
          onError: (detail) => setError(detail),
        },
        ac.signal,
      )
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setComputing(false)
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <h1>推荐历史</h1>
        <p>
          默认只展示归档名单；点击「计算涨跌幅」后按 SSE 逐只推送，算完一只展示一只。
        </p>
      </div>

      {accuracy ? (
        <div className="stat-row">
          <div className="stat">
            <span className="metric-label">买入命中率</span>
            <strong className="metric-value">{formatPct(accuracy.buy_hit_rate, 1)}</strong>
          </div>
          <div className="stat">
            <span className="metric-label">全部命中率</span>
            <strong className="metric-value">{formatPct(accuracy.all_hit_rate, 1)}</strong>
          </div>
          <div className="stat">
            <span className="metric-label">样本</span>
            <strong className="metric-value">
              {accuracy.buy_n}/{accuracy.all_n}
            </strong>
          </div>
        </div>
      ) : null}

      <div className="form-actions form-actions--aligned">
        <label>
          推荐日
          <select
            className="input"
            value={tradeDate}
            onChange={(e) => setTradeDate(e.target.value)}
          >
            {dates.length === 0 ? <option value="">暂无归档</option> : null}
            {dates.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label>
          相对日（默认今天）
          <input
            className="input"
            type="date"
            value={vsDate}
            onChange={(e) => setVsDate(e.target.value)}
          />
        </label>
        <div className="field-actions">
          <button
            className="btn"
            type="button"
            disabled={!tradeDate || computing || loading || items.length === 0}
            onClick={computeReturns}
          >
            {computing
              ? `计算中 ${progress ? `${progress.done}/${progress.total}` : '…'}`
              : '计算涨跌幅'}
          </button>
          <button className="btn ghost" type="button" onClick={() => setVsDate('')}>
            重置为今天
          </button>
          {computing ? (
            <button
              className="btn ghost"
              type="button"
              onClick={() => abortRef.current?.abort()}
            >
              停止
            </button>
          ) : null}
        </div>
      </div>

      {loading ? <p className="status">加载归档…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {accuracy?.note ? <p className="meta-line">{accuracy.note}</p> : null}

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>评分</th>
              <th>建议</th>
              <th>推荐收盘</th>
              <th>相对日收盘</th>
              <th>涨跌幅</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.symbol}>
                <td className="mono">{it.symbol}</td>
                <td>{it.name}</td>
                <td>{formatScore(it.score)}</td>
                <td>
                  <ActionBadge action={it.action} label={it.action_label || it.action} />
                </td>
                <td>{it.base_close ?? it.close ?? '—'}</td>
                <td>{it.vs_close ?? (computing ? '…' : '—')}</td>
                <td
                  className={
                    it.return_pct == null
                      ? ''
                      : (it.return_pct || 0) >= 0
                        ? 'up'
                        : 'down'
                  }
                >
                  {it.return_pct == null
                    ? computing
                      ? '…'
                      : '—'
                    : formatPct(it.return_pct, 2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
