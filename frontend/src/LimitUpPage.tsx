import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { PageNav } from './components/PageNav'
import { useSources } from './hooks/useSources'
import {
  fetchLimitUp,
  formatDayChgPct,
  shouldShowTodayTable,
  statusLabel,
  trendClass,
  type LimitUpResponse,
} from './limitUpApi'
import { DEFAULT_SOURCE, hasFeature } from './sources'

const POLL_MS = 10_000

export default function LimitUpPage() {
  const params = useParams()
  const source = (params.source || DEFAULT_SOURCE).toLowerCase()
  const sources = useSources()
  const sourceMeta = sources.find((s) => s.id === source)

  const [data, setData] = useState<LimitUpResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetchLimitUp(source)
      setData(res)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [source])

  useEffect(() => {
    setLoading(true)
    void load()
  }, [load])

  useEffect(() => {
    const isTrading = Boolean(data?.session?.is_trading)
    if (!isTrading) return

    const tick = () => {
      if (document.visibilityState === 'hidden') return
      void load()
    }
    const id = window.setInterval(tick, POLL_MS)
    const onVis = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [data?.session?.is_trading, load])

  if (sourceMeta && !hasFeature(sourceMeta, 'limitup')) {
    return <Navigate to={`/${source}`} replace />
  }

  const isTrading = shouldShowTodayTable(data?.session)
  const today = data?.today ?? []
  const ladder = data?.ladder ?? []

  return (
    <div className="app limitup-app">
      <header className="topbar">
        <div className="brand">
          <strong>Share Data</strong>
          <span>打板</span>
        </div>
        <PageNav sources={sources} activeFeature="limitup" />
      </header>

      <main className="limitup-main">
        <div className="limitup-toolbar">
          <div>
            <h1 className="section-title">打板</h1>
            <p className="meta-line">
              {data?.date ? `池日期 ${data.date}` : '—'}
              {data?.as_of ? ` · 更新 ${new Date(data.as_of).toLocaleString('zh-CN', { hour12: false })}` : ''}
              {isTrading ? ' · 交易中 · 约 10 秒刷新' : ' · 非交易时段'}
            </p>
          </div>
          <button
            type="button"
            className="btn-primary"
            onClick={() => void load()}
            disabled={loading}
          >
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>

        {error ? <div className="error-banner">{error}</div> : null}

        {loading && !data ? (
          <div className="table-empty">正在拉取涨停数据…</div>
        ) : (
          <>
            <section className="limitup-section" aria-label="当天涨停">
              <h2>当天涨停</h2>
              {!isTrading ? (
                <p className="muted" data-testid="today-hidden">
                  非交易时段不展示「当天涨停」。连板看板仍可查看。
                </p>
              ) : (
                <div className="table-wrap">
                  <table className="limitup-table">
                    <thead>
                      <tr>
                        <th>标记</th>
                        <th>名称</th>
                        <th>代码</th>
                        <th>当日涨幅</th>
                        <th>连板</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {today.map((row) => {
                        const cls = trendClass(row.day_chg_pct)
                        return (
                          <tr key={row.symbol}>
                            <td>
                              <span
                                className={
                                  row.status === 'sealed'
                                    ? 'limitup-badge sealed'
                                    : 'limitup-badge broken'
                                }
                              >
                                {statusLabel(row.status)}
                              </span>
                            </td>
                            <td>{row.name}</td>
                            <td>
                              <code>{row.symbol}</code>
                            </td>
                            <td className={cls}>{formatDayChgPct(row.day_chg_pct)}</td>
                            <td>{row.board_count}</td>
                            <td>
                              <Link
                                className="text-link"
                                to={`/${source}/kline?symbol=${row.symbol}&range=daily`}
                              >
                                K线
                              </Link>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                  {today.length === 0 ? (
                    <p className="table-empty">暂无涨停/炸板数据</p>
                  ) : null}
                </div>
              )}
            </section>

            <section className="limitup-section" aria-label="连板看板">
              <h2>连板看板</h2>
              {ladder.length === 0 ? (
                <p className="muted">暂无连板数据</p>
              ) : (
                <div className="limitup-ladder">
                  {ladder.map((tier) => (
                    <div key={tier.board_count} className="limitup-tier">
                      <h3>{tier.board_count} 连板</h3>
                      <ul>
                        {tier.items.map((item) => (
                          <li key={item.symbol}>
                            <span className="limitup-tier-name">{item.name}</span>
                            <code>{item.symbol}</code>
                            <span className={trendClass(item.day_chg_pct)}>
                              {formatDayChgPct(item.day_chg_pct)}
                            </span>
                            <Link
                              className="text-link"
                              to={`/${source}/kline?symbol=${item.symbol}&range=daily`}
                            >
                              K线
                            </Link>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  )
}
