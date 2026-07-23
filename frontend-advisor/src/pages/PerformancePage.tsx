import { useEffect, useRef, useState } from 'react'
import {
  formatPct,
  streamBacktestSummary,
  type BacktestSummary,
} from '../api'

type Progress = {
  done: number
  total: number
  phase?: string
  symbol?: string
  name?: string
  cached?: boolean
  message?: string
}

export default function PerformancePage() {
  const [data, setData] = useState<BacktestSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const reqIdRef = useRef(0)

  async function load(force = false) {
    const reqId = ++reqIdRef.current
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setLoading(true)
    setError(null)
    setStatus(force ? '正在重新回测…' : '正在加载策略表现…')
    setProgress({ done: 0, total: 0, phase: 'prepare' })

    try {
      await streamBacktestSummary(
        force,
        {
          onMeta: (meta) => {
            if (reqId !== reqIdRef.current) return
            setProgress({
              done: 0,
              total: meta.total,
              phase: meta.phase,
              cached: meta.cached,
            })
            if (meta.cached) setStatus('命中缓存…')
            else if (meta.phase === 'universe' || meta.phase === 'prepare')
              setStatus('拉取全市场候选池（ETF + 沪深）…')
            else setStatus('回测进行中…')
          },
          onProgress: (row) => {
            if (reqId !== reqIdRef.current) return
            setProgress({
              done: row.done,
              total: row.total,
              phase: row.phase,
              symbol: row.symbol,
              name: row.name,
              message: row.message,
            })
            if (row.phase === 'universe') {
              setStatus(row.message || '拉取候选池…')
              return
            }
            const label =
              row.phase === 'akquant'
                ? 'AKQuant'
                : row.phase === 'event_study'
                  ? '事件研究'
                  : '回测'
            setStatus(
              row.symbol
                ? `${label} ${row.done}/${row.total} · ${row.name || row.symbol}`
                : `${label} ${row.done}/${row.total}`,
            )
          },
          onDone: (summary) => {
            if (reqId !== reqIdRef.current) return
            setData(summary)
            setProgress(null)
            setStatus(
              summary.from_cache
                ? `已加载缓存 · ${summary.as_of}`
                : `回测完成 · ${summary.as_of}`,
            )
          },
          onError: (detail) => {
            if (reqId !== reqIdRef.current) return
            setError(detail)
            setStatus(null)
          },
        },
        ac.signal,
      )
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      if (reqId !== reqIdRef.current) return
      setError(err instanceof Error ? err.message : String(err))
      setStatus(null)
    } finally {
      if (reqId === reqIdRef.current) {
        setLoading(false)
        setProgress(null)
      }
    }
  }

  useEffect(() => {
    load(false)
    return () => {
      reqIdRef.current += 1
      abortRef.current?.abort()
    }
  }, [])

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.done / progress.total) * 100))
      : progress?.cached
        ? 100
        : progress
          ? 5
          : 0

  return (
    <section className="page">
      <div className="page-hero">
        <h1>策略表现</h1>
        <p>
          事件研究：高分信号次日上涨命中率；并在样本上用 AKQuant 校验收益与回撤。
        </p>
      </div>

      <div className="form-actions">
        <button className="btn" type="button" disabled={loading} onClick={() => load(true)}>
          {loading ? '计算中…' : '重新回测'}
        </button>
      </div>

      {loading ? (
        <div className="progress-bar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="progress-bar-fill" style={{ width: `${Math.max(pct, 4)}%` }} />
          <span className="progress-bar-label">
            {progress?.cached
              ? '命中缓存，即将完成…'
              : progress && progress.total > 0
                ? status || `${progress.done}/${progress.total}`
                : status || '准备中…'}
          </span>
        </div>
      ) : null}

      {!loading && status ? <p className="meta-line">{status}</p> : null}
      {error ? <p className="status error">{error}</p> : null}

      {data ? (
        <>
          <div className="stat-row">
            <div className="stat">
              <span className="metric-label">次日命中率</span>
              <strong className="metric-value">{formatPct(data.hit_rate)}</strong>
            </div>
            <div className="stat">
              <span className="metric-label">平均次日收益</span>
              <strong className="metric-value">{formatPct(data.avg_next_ret, 2)}</strong>
            </div>
            <div className="stat">
              <span className="metric-label">AKQuant 收益%</span>
              <strong className="metric-value">
                {data.akquant_avg_return_pct == null
                  ? '—'
                  : data.akquant_avg_return_pct.toFixed(2)}
              </strong>
            </div>
            <div className="stat">
              <span className="metric-label">AKQuant 回撤%</span>
              <strong className="metric-value">
                {data.akquant_avg_max_drawdown_pct == null
                  ? '—'
                  : data.akquant_avg_max_drawdown_pct.toFixed(2)}
              </strong>
            </div>
          </div>

          <p className="meta-line">
            引擎 {data.engine} · 信号 {data.n_signals} · 标的 {data.symbols_tested} ·
            阈值 {data.threshold} · 更新于 {data.as_of}
            {data.from_cache ? ' · 缓存' : ''}
            {data.akquant?.akquant_installed ? ' · AKQuant 已安装' : ' · AKQuant 未安装'}
          </p>

          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>信号数</th>
                  <th>命中率</th>
                  <th>平均次日收益</th>
                </tr>
              </thead>
              <tbody>
                {data.per_symbol.map((row) => (
                  <tr key={row.symbol}>
                    <td className="mono">{row.symbol}</td>
                    <td>{row.name}</td>
                    <td>{row.n_signals}</td>
                    <td>{formatPct(row.hit_rate)}</td>
                    <td>{formatPct(row.avg_next_ret, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  )
}
