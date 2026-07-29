import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchWatchlistMarks,
  formatPct,
  removeWatchlist,
  type WatchlistMarksResponse,
} from '../api'
import { StarToggle } from '../components/StarToggle'
import { explorerKlineUrl } from '../explorerLinks'

function chgClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return ''
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return ''
}

function formatPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return Number(v).toFixed(3)
}

export default function WatchlistPage() {
  const [marks, setMarks] = useState<WatchlistMarksResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [marksLoading, setMarksLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyMap, setBusyMap] = useState<Record<string, boolean>>({})

  const loadMarks = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setMarksLoading(true)
    try {
      const res = await fetchWatchlistMarks()
      setMarks(res)
      setError(null)
      return res
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return null
    } finally {
      setLoading(false)
      if (!opts?.silent) setMarksLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer: number | null = null
    let first = true

    const schedule = (isTrading: boolean) => {
      if (cancelled || !isTrading) return
      timer = window.setTimeout(() => {
        void tick()
      }, 3000)
    }

    const tick = async () => {
      try {
        if (first) setMarksLoading(true)
        const res = await fetchWatchlistMarks()
        if (cancelled) return
        setMarks(res)
        setError(null)
        schedule(Boolean(res.session?.is_trading))
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        first = false
        if (!cancelled) {
          setLoading(false)
          setMarksLoading(false)
        }
      }
    }

    void tick()
    return () => {
      cancelled = true
      if (timer != null) window.clearTimeout(timer)
    }
  }, [])

  async function onUnstar(symbol: string) {
    setBusyMap((prev) => ({ ...prev, [symbol]: true }))
    const prev = marks
    setMarks((cur) =>
      cur
        ? {
            ...cur,
            count: Math.max(0, cur.count - 1),
            items: cur.items.filter((it) => it.symbol !== symbol),
          }
        : cur,
    )
    try {
      await removeWatchlist(symbol)
      await loadMarks({ silent: true })
    } catch (err) {
      setMarks(prev)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyMap((cur) => {
        const copy = { ...cur }
        delete copy[symbol]
        return copy
      })
    }
  }

  const trading = Boolean(marks?.session?.is_trading)

  return (
    <section className="page">
      <div className="page-hero">
        <p>
          收藏标的盘中约 3 秒刷新行情；非交易时段进入页面刷新一次。可从今日关注或股票诊断加星。
        </p>
      </div>

      <div className="diag-block">
        <div className="form-actions" style={{ marginTop: 0 }}>
          <h2 className="section-title">我的收藏</h2>
          <button
            type="button"
            className="btn ghost"
            disabled={marksLoading || loading}
            onClick={() => void loadMarks()}
          >
            {marksLoading ? '刷新中…' : '刷新行情'}
          </button>
        </div>
        <p className="meta-line">
          {marks?.updated_at ? `更新 ${marks.updated_at}` : '尚未刷新'}
          {' · '}
          {trading ? '交易中 · 约 3 秒自动刷新' : '非交易时段 · 进入页面刷新一次'}
          {marks ? ` · 共 ${marks.count} 只` : ''}
        </p>
        {error ? <p className="status error">{error}</p> : null}
        {loading || (marksLoading && !marks) ? (
          <p className="status">正在拉取收藏…</p>
        ) : null}
        {marks && marks.items.length === 0 ? (
          <p className="muted">暂无收藏。可在「今日关注」或「股票诊断」点星加入。</p>
        ) : null}
        {marks && marks.items.length > 0 ? (
          <div className="table-wrap">
            <table className="data-table portfolio-marks-table">
              <thead>
                <tr>
                  <th>名称/代码</th>
                  <th>现价</th>
                  <th>涨跌幅</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {marks.items.map((item) => (
                  <tr key={item.symbol}>
                    <td>
                      <div className="cell-main">{item.name || '—'}</div>
                      <div className="cell-sub mono">{item.symbol}</div>
                    </td>
                    <td>
                      <div className={`cell-main ${chgClass(item.day_chg_pct)}`}>
                        {formatPrice(item.price)}
                      </div>
                    </td>
                    <td className={chgClass(item.day_chg_pct)}>
                      <div className="cell-main">
                        {item.day_chg_pct == null
                          ? '—'
                          : formatPct(item.day_chg_pct, 2)}
                      </div>
                    </td>
                    <td className="row-actions">
                      <StarToggle
                        symbol={item.symbol}
                        starred
                        busy={Boolean(busyMap[item.symbol])}
                        onToggle={(next) => {
                          if (!next) void onUnstar(item.symbol)
                        }}
                      />
                      <Link className="text-link" to={`/advice?symbol=${item.symbol}`}>
                        诊断
                      </Link>
                      <a
                        className="text-link"
                        href={explorerKlineUrl(item.symbol)}
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
        ) : null}
      </div>
    </section>
  )
}
