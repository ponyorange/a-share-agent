import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  streamLeaderboard,
  type LeaderboardBoardId,
  type LeaderboardItem,
  type LeaderboardListId,
  type LeaderboardPayload,
} from '../api'
import { explorerKlineUrl } from '../explorerLinks'

const LIST_TABS: { id: LeaderboardListId; label: string }[] = [
  { id: 'gainers', label: '涨幅榜' },
  { id: 'losers', label: '跌幅榜' },
  { id: 'inflow', label: '资金流入榜' },
  { id: 'outflow', label: '资金流出榜' },
]

const BOARD_TABS: { id: LeaderboardBoardId; label: string }[] = [
  { id: 'etf', label: 'ETF' },
  { id: 'hs', label: '沪深股' },
  { id: 'star', label: '科创股' },
]

type Progress = {
  done: number
  total: number
  cached?: boolean
  label?: string
}

function chgClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return ''
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return ''
}

/** 东财 f3 为百分点，如 2.5 → 2.50% */
function formatChgPts(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function formatMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  const abs = Math.abs(v)
  const sign = v > 0 ? '+' : v < 0 ? '' : ''
  if (abs >= 1e8) return `${sign}${(v / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${sign}${(v / 1e4).toFixed(2)}万`
  return `${sign}${v.toFixed(0)}`
}

function RankTable({
  items,
  mode,
}: {
  items: LeaderboardItem[]
  mode: 'chg' | 'flow'
}) {
  if (!items.length) {
    return <p className="status">本板暂无数据。</p>
  }
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>#</th>
            <th>代码</th>
            <th>名称</th>
            <th>现价</th>
            <th>涨跌幅</th>
            {mode === 'flow' ? <th>主力净流入</th> : <th>成交额</th>}
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => (
            <tr key={row.symbol}>
              <td className="mono">{i + 1}</td>
              <td className="mono">{row.symbol}</td>
              <td>{row.name}</td>
              <td>{row.price == null ? '—' : row.price.toFixed(2)}</td>
              <td className={chgClass(row.pct_chg)}>{formatChgPts(row.pct_chg)}</td>
              {mode === 'flow' ? (
                <td className={chgClass(row.main_net_inflow)}>
                  {formatMoney(row.main_net_inflow)}
                </td>
              ) : (
                <td>{formatMoney(row.amount)}</td>
              )}
              <td className="row-actions">
                <Link className="text-link" to={`/advice?symbol=${row.symbol}`}>
                  诊断
                </Link>
                <a
                  className="text-link"
                  href={explorerKlineUrl(row.symbol)}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看K线
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function LeaderboardPage() {
  const [data, setData] = useState<LeaderboardPayload | null>(null)
  const [listId, setListId] = useState<LeaderboardListId>('gainers')
  const [boardId, setBoardId] = useState<LeaderboardBoardId>('etf')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const reqIdRef = useRef(0)

  async function load(force: boolean) {
    const reqId = ++reqIdRef.current
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setLoading(true)
    setError(null)
    setStatus(force ? '正在拉取榜单…' : '正在加载…')
    setProgress({ done: 0, total: 0 })

    try {
      await streamLeaderboard(
        force,
        {
          onMeta: (meta) => {
            if (reqId !== reqIdRef.current) return
            setProgress({
              done: 0,
              total: meta.total,
              cached: meta.cached,
            })
            if (meta.cached) setStatus('命中当日缓存…')
            else setStatus(`拉取行情 · ${meta.trade_date || ''}`)
          },
          onProgress: (row) => {
            if (reqId !== reqIdRef.current) return
            setProgress({
              done: row.done,
              total: row.total,
              label: row.label,
            })
            setStatus(
              row.label
                ? `${row.done}/${row.total} · ${row.label}${row.ok === false ? '（失败）' : ''}`
                : `${row.done}/${row.total}`,
            )
          },
          onDone: (payload) => {
            if (reqId !== reqIdRef.current) return
            setData(payload)
            setProgress(null)
            setStatus(
              payload.from_cache
                ? `已加载缓存 · ${payload.trade_date}${payload.as_of ? ` · ${payload.as_of}` : ''}`
                : `拉取完成 · ${payload.trade_date}${payload.as_of ? ` · ${payload.as_of}` : ''}`,
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

  const items = data?.boards?.[listId]?.[boardId] || []
  const flowMode = listId === 'inflow' || listId === 'outflow'
  const errHint =
    data?.errors?.length && !loading
      ? `部分榜单拉取失败 ${data.errors.length} 项`
      : null

  return (
    <section className="page">
      <div className="page-hero">
        <h1>龙虎榜</h1>
        <p>涨跌幅榜与资金流入流出榜，按 ETF / 沪深 / 科创分板，各取 Top 25，按交易日缓存。</p>
      </div>

      <div className="form-actions">
        <div className="board-tabs" role="tablist" aria-label="榜单类型">
          {LIST_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              className={`board-tab${listId === t.id ? ' active' : ''}`}
              aria-selected={listId === t.id}
              onClick={() => setListId(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button className="btn" type="button" disabled={loading} onClick={() => load(true)}>
          {loading ? '拉取中…' : '拉取榜单'}
        </button>
      </div>

      <div className="form-actions">
        <div className="board-tabs" role="tablist" aria-label="板块">
          {BOARD_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              className={`board-tab${boardId === t.id ? ' active' : ''}`}
              aria-selected={boardId === t.id}
              onClick={() => setBoardId(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
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
      {errHint ? <p className="status error">{errHint}</p> : null}

      {!loading && !data?.boards ? (
        <p className="status">暂无缓存，请点击「拉取榜单」。</p>
      ) : null}

      {data?.boards ? <RankTable items={items} mode={flowMode ? 'flow' : 'chg'} /> : null}
    </section>
  )
}
