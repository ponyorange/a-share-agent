import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { PageNav } from './components/PageNav'
import { useSources } from './hooks/useSources'
import {
  fetchMarket,
  formatAmountYi,
  formatChange,
  formatIndexPrice,
  formatPct,
  trendClass,
  type BoardStock,
  type MarketIndex,
  type MarketResponse,
} from './marketApi'
import { DEFAULT_SOURCE, hasFeature } from './sources'

function BoardPanel({
  title,
  rows,
  mode,
  source,
}: {
  title: string
  rows: BoardStock[]
  mode: 'up' | 'down' | 'amount'
  source: string
}) {
  return (
    <section className={`market-board market-board-${mode}`}>
      <h2>{title}</h2>
      <ol className="market-board-list">
        {rows.map((row) => {
          const cls = trendClass(row.change_pct)
          return (
            <li key={`${mode}-${row.symbol}`}>
              <Link
                to={`/${source}/kline?symbol=${row.symbol}&range=daily`}
                className="market-board-row"
              >
                <span className="market-board-rank">{row.rank}</span>
                <span className="market-board-name">
                  <strong>{row.name}</strong>
                  <code>{row.symbol}</code>
                </span>
                <span className={`market-board-price ${cls}`}>
                  {formatIndexPrice(row.price)}
                </span>
                {mode === 'amount' ? (
                  <span className="market-board-amt">{formatAmountYi(row.amount)}</span>
                ) : null}
                <span className={`market-board-pct ${cls}`}>
                  {formatPct(row.change_pct)}
                </span>
              </Link>
            </li>
          )
        })}
      </ol>
      {rows.length === 0 ? (
        <p className="market-board-empty">暂无数据</p>
      ) : null}
    </section>
  )
}

function IndexTile({ item }: { item: MarketIndex }) {
  const cls = trendClass(item.change_pct)
  return (
    <article className={`market-tile ${cls}`}>
      <header>
        <h3>{item.name}</h3>
        <code>{item.symbol}</code>
      </header>
      <p className={`market-tile-price ${cls}`}>{formatIndexPrice(item.price)}</p>
      <p className={`market-tile-chg ${cls}`}>
        <span>{formatChange(item.change)}</span>
        <span>{formatPct(item.change_pct)}</span>
      </p>
      <dl className="market-tile-meta">
        <div>
          <dt>今开</dt>
          <dd>{formatIndexPrice(item.open)}</dd>
        </div>
        <div>
          <dt>最高</dt>
          <dd>{formatIndexPrice(item.high)}</dd>
        </div>
        <div>
          <dt>最低</dt>
          <dd>{formatIndexPrice(item.low)}</dd>
        </div>
        <div>
          <dt>额</dt>
          <dd>{formatAmountYi(item.amount)}</dd>
        </div>
      </dl>
    </article>
  )
}

export default function MarketPage() {
  const params = useParams()
  const source = (params.source || DEFAULT_SOURCE).toLowerCase()
  const sources = useSources()
  const sourceMeta = sources.find((s) => s.id === source)
  const [data, setData] = useState<MarketResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchMarket(source))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [source])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    const t = window.setInterval(() => {
      void load()
    }, 30000)
    return () => window.clearInterval(t)
  }, [load])

  const updatedLabel = data?.updated_at
    ? new Date(data.updated_at).toLocaleString('zh-CN', { hour12: false })
    : null

  if (sourceMeta && !hasFeature(sourceMeta, 'market')) {
    return <Navigate to={`/${source}`} replace />
  }

  return (
    <div className="app market-app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1>{sourceMeta?.label || source} 大盘</h1>
            <p className="brand-sub">主要指数实时行情</p>
          </div>
        </div>
        <PageNav sources={sources} activeFeature="market" />
      </header>

      <main className="market-main">
        <div className="market-toolbar">
          <div className="market-summary">
            <span>
              沪市成交{' '}
              <strong>{formatAmountYi(data?.summary.amount_sh)}</strong>
            </span>
            <span>
              深市成交{' '}
              <strong>{formatAmountYi(data?.summary.amount_sz)}</strong>
            </span>
            <span>
              两市合计{' '}
              <strong>{formatAmountYi(data?.summary.amount_total)}</strong>
            </span>
          </div>
          <div className="market-toolbar-right">
            {updatedLabel ? (
              <span className="market-updated">更新于 {updatedLabel}</span>
            ) : null}
            {data?.source ? (
              <span className="market-source">{data.source}</span>
            ) : null}
            <button
              type="button"
              className="btn-primary"
              onClick={() => void load()}
              disabled={loading}
            >
              {loading ? '刷新中…' : '刷新'}
            </button>
          </div>
        </div>

        {error ? <div className="error-banner">{error}</div> : null}

        {loading && !data ? (
          <div className="table-empty">正在拉取大盘行情…</div>
        ) : (
          <>
            <section className="market-featured" aria-label="主要指数">
              {(data?.featured ?? []).map((item) => (
                <IndexTile key={item.symbol} item={item} />
              ))}
            </section>

            <section className="market-boards" aria-label="涨跌榜">
              {data?.boards?.error ? (
                <div className="error-banner">涨跌榜：{data.boards.error}</div>
              ) : null}
              <BoardPanel
                title="涨幅榜"
                rows={data?.boards?.gainers ?? []}
                mode="up"
                source={source}
              />
              <BoardPanel
                title="跌幅榜"
                rows={data?.boards?.losers ?? []}
                mode="down"
                source={source}
              />
              <BoardPanel
                title="成交额榜"
                rows={data?.boards?.amount ?? []}
                mode="amount"
                source={source}
              />
            </section>

            <section className="market-table-section" aria-label="指数列表">
              <h2>全部关注指数</h2>
              <div className="table-wrap market-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th>名称</th>
                      <th>最新</th>
                      <th>涨跌额</th>
                      <th>涨跌幅</th>
                      <th>今开</th>
                      <th>最高</th>
                      <th>最低</th>
                      <th>昨收</th>
                      <th>成交额</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data?.indices ?? []).map((row) => {
                      const cls = trendClass(row.change_pct)
                      return (
                        <tr key={row.symbol} className={cls}>
                          <td>
                            <code>{row.symbol}</code>
                          </td>
                          <td>{row.name}</td>
                          <td className={cls}>{formatIndexPrice(row.price)}</td>
                          <td className={cls}>{formatChange(row.change)}</td>
                          <td className={cls}>{formatPct(row.change_pct)}</td>
                          <td>{formatIndexPrice(row.open)}</td>
                          <td>{formatIndexPrice(row.high)}</td>
                          <td>{formatIndexPrice(row.low)}</td>
                          <td>{formatIndexPrice(row.pre_close)}</td>
                          <td>{formatAmountYi(row.amount)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}
