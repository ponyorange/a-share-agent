import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchHomeSectors,
  fetchLimitUp,
  fetchMarket,
  fetchRegimeSummary,
  type HomeSectorsResponse,
  type LimitUpResponse,
  type MarketResponse,
  type RegimeCurrent,
} from '../api'
import { explorerKlineUrl } from '../explorerLinks'
import {
  formatCapPct,
  gateOneLiner,
  gateShortLabel,
  sentimentLabel,
  trendLabel,
} from '../regimeCopy'

type TileState<T> =
  | { status: 'loading' }
  | { status: 'ok'; data: T }
  | { status: 'error'; error: string }

const INDEX_PRIORITY = ['000300', '000001', '399001', '399006', '000688', '000016']

function formatPct(raw: number | null | undefined, digits = 2): string {
  if (raw == null || Number.isNaN(Number(raw))) return '—'
  const n = Number(raw)
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(digits)}%`
}

function metricNum(metrics: Record<string, unknown> | undefined, key: string): number | null {
  const v = metrics?.[key]
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function pickFeatured(market: MarketResponse) {
  const featured = [...(market.featured || [])]
  featured.sort((a, b) => {
    const ia = INDEX_PRIORITY.indexOf(a.symbol)
    const ib = INDEX_PRIORITY.indexOf(b.symbol)
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib)
  })
  return featured.slice(0, 6)
}

function Tile<T>({
  title,
  state,
  onRetry,
  children,
}: {
  title: string
  state: TileState<T>
  onRetry?: () => void
  children: (data: T) => ReactNode
}) {
  return (
    <section className="home-tile" aria-label={title}>
      <h3 className="home-tile-title">{title}</h3>
      {state.status === 'loading' ? <div className="home-tile-skeleton" /> : null}
      {state.status === 'error' ? (
        <div className="home-tile-error">
          <p className="status error">{state.error}</p>
          {onRetry ? (
            <button type="button" className="btn ghost" onClick={onRetry}>
              重试
            </button>
          ) : null}
        </div>
      ) : null}
      {state.status === 'ok' ? children(state.data) : null}
    </section>
  )
}

export default function HomePage() {
  const [market, setMarket] = useState<TileState<MarketResponse>>({ status: 'loading' })
  const [regime, setRegime] = useState<TileState<RegimeCurrent>>({ status: 'loading' })
  const [limitUp, setLimitUp] = useState<TileState<LimitUpResponse>>({ status: 'loading' })
  const [sectors, setSectors] = useState<TileState<HomeSectorsResponse>>({
    status: 'loading',
  })

  const loadMarket = () => {
    setMarket({ status: 'loading' })
    fetchMarket()
      .then((data) => setMarket({ status: 'ok', data }))
      .catch((err) =>
        setMarket({
          status: 'error',
          error: err instanceof Error ? err.message : String(err),
        }),
      )
  }

  const loadRegime = () => {
    setRegime({ status: 'loading' })
    fetchRegimeSummary()
      .then((data) => setRegime({ status: 'ok', data }))
      .catch((err) =>
        setRegime({
          status: 'error',
          error: err instanceof Error ? err.message : String(err),
        }),
      )
  }

  const loadLimitUp = () => {
    setLimitUp({ status: 'loading' })
    fetchLimitUp()
      .then((data) => setLimitUp({ status: 'ok', data }))
      .catch((err) =>
        setLimitUp({
          status: 'error',
          error: err instanceof Error ? err.message : String(err),
        }),
      )
  }

  const loadSectors = () => {
    setSectors({ status: 'loading' })
    fetchHomeSectors(8)
      .then((data) => setSectors({ status: 'ok', data }))
      .catch((err) =>
        setSectors({
          status: 'error',
          error: err instanceof Error ? err.message : String(err),
        }),
      )
  }

  useEffect(() => {
    loadMarket()
    loadRegime()
    loadLimitUp()
    loadSectors()
  }, [])

  const metaBits: string[] = []
  if (regime.status === 'ok' && regime.data.trade_date) {
    metaBits.push(`闸门日 ${regime.data.trade_date}`)
  }
  if (market.status === 'ok' && market.data.as_of) {
    metaBits.push(`行情 ${market.data.as_of}`)
  }
  if (sectors.status === 'ok' && sectors.data.trade_date) {
    metaBits.push(`板块 ${sectors.data.trade_date}`)
  }

  const metrics = regime.status === 'ok' ? regime.data.metrics : undefined
  const breadth = metricNum(metrics, 'breadth')
  const maxBoard = metricNum(metrics, 'max_board')
  const promotion = metricNum(metrics, 'promotion_rate')
  const limitUpCount = metricNum(metrics, 'limit_up_count')
  const ladderMax =
    limitUp.status === 'ok'
      ? Math.max(0, ...(limitUp.data.ladder || []).map((t) => t.board_count || 0))
      : null

  return (
    <section className="page home-page">
      <div className="page-hero">
        <h2 className="section-title">市场首页</h2>
        {metaBits.length ? <p className="meta-line">{metaBits.join(' · ')}</p> : null}
      </div>

      <div className="home-grid">
        <Tile title="主要指数" state={market} onRetry={loadMarket}>
          {(data) => {
            const rows = pickFeatured(data)
            if (!rows.length) {
              return <p className="muted">暂无指数数据</p>
            }
            return (
              <ul className="home-index-list">
                {rows.map((row) => (
                  <li key={row.symbol}>
                    <a
                      className="text-link"
                      href={explorerKlineUrl(row.symbol)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {row.name}
                    </a>
                    <span className="mono">
                      {row.price != null ? Number(row.price).toFixed(2) : '—'}
                    </span>
                    <span
                      className={
                        (row.change_pct ?? 0) > 0
                          ? 'chg up'
                          : (row.change_pct ?? 0) < 0
                            ? 'chg down'
                            : 'chg'
                      }
                    >
                      {formatPct(row.change_pct)}
                    </span>
                  </li>
                ))}
              </ul>
            )
          }}
        </Tile>

        <Tile title="趋势 · 情绪 · 闸门" state={regime} onRetry={loadRegime}>
          {(data) => (
            <div className="home-regime">
              <p className="meta-line">
                <span>趋势 {trendLabel(data.trend_regime)}</span>
                {' · '}
                <span>情绪 {sentimentLabel(data.sentiment_cycle)}</span>
                {' · '}
                <strong>{gateShortLabel(data.gate_level)}</strong>
              </p>
              <p>{gateOneLiner(data.gate_level)}</p>
              <p className="meta-line">
                建议总仓位不超过 {formatCapPct(data.position_cap)}
              </p>
              <Link className="text-link" to="/regime">
                查看今日闸门
              </Link>
            </div>
          )}
        </Tile>

        <Tile
          title="涨跌分布"
          state={
            regime.status === 'loading'
              ? { status: 'loading' }
              : regime.status === 'error'
                ? regime
                : {
                    status: 'ok',
                    data: { breadth },
                  }
          }
          onRetry={loadRegime}
        >
          {() => (
            <div>
              <p>
                上涨家数占比{' '}
                <strong className="mono">
                  {breadth == null ? '—' : `${(breadth * 100).toFixed(1)}%`}
                </strong>
              </p>
              <p className="muted">摘要 · 来自市场闸门 breadth</p>
            </div>
          )}
        </Tile>

        <Tile
          title="情绪结构 · 热点"
          state={
            sectors.status === 'loading' && limitUp.status === 'loading' && regime.status === 'loading'
              ? { status: 'loading' }
              : sectors.status === 'error' &&
                  limitUp.status === 'error' &&
                  regime.status === 'error'
                ? { status: 'error', error: '热点与涨停数据暂不可用' }
                : {
                    status: 'ok',
                    data: true,
                  }
          }
          onRetry={() => {
            loadSectors()
            loadLimitUp()
            loadRegime()
          }}
        >
          {() => (
            <div className="home-hot">
              <p className="meta-line">
                最高连板{' '}
                <strong className="mono">
                  {maxBoard ?? ladderMax ?? '—'}
                </strong>
                {' · '}
                晋级率{' '}
                <strong className="mono">
                  {promotion == null ? '—' : `${(promotion * 100).toFixed(1)}%`}
                </strong>
                {' · '}
                涨停{' '}
                <strong className="mono">{limitUpCount ?? '—'}</strong>
              </p>
              {sectors.status === 'ok' && sectors.data.items.length ? (
                <ul className="home-sector-list">
                  {sectors.data.items.slice(0, 6).map((item) => (
                    <li key={item.name}>
                      <span>
                        {item.rank}. {item.name}
                      </span>
                      <span className="mono">{formatPct(item.change_pct)}</span>
                    </li>
                  ))}
                </ul>
              ) : sectors.status === 'loading' ? (
                <div className="home-tile-skeleton" />
              ) : (
                <p className="muted">热点题材暂不可用</p>
              )}
              <Link className="text-link" to="/limitup">
                打开打板
              </Link>
            </div>
          )}
        </Tile>
      </div>
    </section>
  )
}
