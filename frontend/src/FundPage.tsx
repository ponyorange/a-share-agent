import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { Navigate, useParams, useSearchParams } from 'react-router-dom'
import { PageNav } from './components/PageNav'
import {
  fetchFundDetail,
  formatNav,
  formatNavPct,
  normalizeFundSymbol,
  searchFunds,
  type FundDetailResponse,
  type FundNavPoint,
  type FundSearchItem,
} from './fundApi'
import { useSources } from './hooks/useSources'
import { trendClass } from './marketApi'
import { DEFAULT_SOURCE, hasFeature } from './sources'

const DEFAULT_FUND = '025857'

function readInitial(sp: URLSearchParams) {
  const raw = normalizeFundSymbol(sp.get('symbol') || DEFAULT_FUND)
  return { symbol: raw.length === 6 ? raw : DEFAULT_FUND }
}

function NavChart({ series }: { series: FundNavPoint[] }) {
  const width = 640
  const height = 180
  const padX = 12
  const padY = 16
  if (series.length < 2) {
    return <div className="table-empty">净值点不足，暂无法绘图</div>
  }
  const vals = series.map((p) => p.nav)
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const span = max - min || 1
  const points = series
    .map((p, i) => {
      const x =
        padX + (i / (series.length - 1)) * (width - padX * 2)
      const y =
        height - padY - ((p.nav - min) / span) * (height - padY * 2)
      return `${x},${y}`
    })
    .join(' ')
  const first = series[0]
  const last = series[series.length - 1]
  return (
    <svg
      className="fund-nav-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="单位净值走势"
    >
      <polyline
        fill="none"
        stroke="var(--accent, #3dcfb6)"
        strokeWidth="2"
        points={points}
      />
      <text x={padX} y={height - 2} className="fund-nav-chart-label">
        {first.date}
      </text>
      <text
        x={width - padX}
        y={height - 2}
        textAnchor="end"
        className="fund-nav-chart-label"
      >
        {last.date}
      </text>
      <text x={padX} y={14} className="fund-nav-chart-label">
        {formatNav(max)}
      </text>
      <text x={padX} y={height - padY - 4} className="fund-nav-chart-label">
        {formatNav(min)}
      </text>
    </svg>
  )
}

export default function FundPage() {
  const routeParams = useParams()
  const source = (routeParams.source || DEFAULT_SOURCE).toLowerCase()
  const sources = useSources()
  const sourceMeta = sources.find((s) => s.id === source)
  const [searchParams, setSearchParams] = useSearchParams()
  const initial = useMemo(() => readInitial(searchParams), [searchParams])
  const [symbolInput, setSymbolInput] = useState(initial.symbol)
  const [symbol, setSymbol] = useState(initial.symbol)
  const [data, setData] = useState<FundDetailResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<FundSearchItem[]>([])
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [suggestEmpty, setSuggestEmpty] = useState(false)
  const debounceRef = useRef<number | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const next = readInitial(searchParams)
    setSymbolInput(next.symbol)
    setSymbol(next.symbol)
  }, [searchParams])

  const load = useCallback(
    async (sym: string) => {
      setLoading(true)
      setError(null)
      try {
        const result = await fetchFundDetail(sym, source)
        setData(result)
      } catch (err) {
        setData(null)
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoading(false)
      }
    },
    [source],
  )

  useEffect(() => {
    void load(symbol)
  }, [symbol, load])

  useEffect(() => {
    const q = symbolInput.trim()
    if (debounceRef.current) window.clearTimeout(debounceRef.current)
    if (q.length < 1) {
      setSuggestions([])
      setSuggestEmpty(false)
      return
    }
    debounceRef.current = window.setTimeout(() => {
      void (async () => {
        try {
          const res = await searchFunds(q, source, 20)
          setSuggestions(res.items)
          setSuggestEmpty(res.items.length === 0)
          setSuggestOpen(true)
        } catch {
          setSuggestions([])
          setSuggestEmpty(false)
        }
      })()
    }, 250)
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current)
    }
  }, [symbolInput, source])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) {
        setSuggestOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const commit = (raw: string) => {
    const clean = normalizeFundSymbol(raw)
    if (clean.length !== 6) {
      setError('请输入 6 位基金代码')
      return
    }
    setSuggestOpen(false)
    setSymbolInput(clean)
    setSymbol(clean)
    setSearchParams({ symbol: clean }, { replace: true })
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    commit(symbolInput)
  }

  const tableRows = useMemo(() => {
    const series = data?.nav?.series || []
    return [...series].reverse()
  }, [data])

  if (sourceMeta && !hasFeature(sourceMeta, 'fund')) {
    return <Navigate to={`/${source}`} replace />
  }

  const ov = data?.overview
  const latest = data?.nav?.latest
  const latestCls = trendClass(latest?.change_pct)

  return (
    <div className="app fund-app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1>{sourceMeta?.label || source} 基金</h1>
            <p className="brand-sub">场外开放式基金档案与净值</p>
          </div>
        </div>
        <PageNav sources={sources} activeFeature="fund" />
      </header>

      <main className="fund-main">
        <form className="fund-search" onSubmit={onSubmit}>
          <div className="fund-search-field" ref={wrapRef}>
            <label>
              <span>搜索</span>
              <input
                value={symbolInput}
                onChange={(e) => setSymbolInput(e.target.value)}
                onFocus={() => {
                  if (suggestions.length || suggestEmpty) setSuggestOpen(true)
                }}
                placeholder="代码 / 简称 / 拼音"
                autoComplete="off"
              />
            </label>
            {suggestOpen && (suggestions.length > 0 || suggestEmpty) ? (
              <ul className="fund-suggest" role="listbox">
                {suggestEmpty ? (
                  <li className="fund-suggest-empty">无匹配基金</li>
                ) : (
                  suggestions.map((item) => (
                    <li key={item.symbol}>
                      <button
                        type="button"
                        onClick={() => commit(item.symbol)}
                      >
                        <strong>{item.name}</strong>
                        <span>
                          <code>{item.symbol}</code> · {item.type}
                        </span>
                      </button>
                    </li>
                  ))
                )}
              </ul>
            ) : null}
          </div>
          <button type="submit">查询</button>
        </form>

        {error ? <div className="error-banner">{error}</div> : null}
        {loading ? (
          <div className="table-empty">正在拉取基金详情…</div>
        ) : null}

        {data && !loading ? (
          <>
            <section className="fund-overview" aria-label="基金档案">
              <header className="fund-overview-head">
                <h2>{data.name}</h2>
                <code>{data.symbol}</code>
              </header>
              {ov ? (
                <dl className="fund-overview-grid">
                  <div>
                    <dt>全称</dt>
                    <dd>{ov.full_name || '—'}</dd>
                  </div>
                  <div>
                    <dt>类型</dt>
                    <dd>{ov.type || '—'}</dd>
                  </div>
                  <div>
                    <dt>成立日期/规模</dt>
                    <dd>{ov.establish_date || '—'}</dd>
                  </div>
                  <div>
                    <dt>净资产</dt>
                    <dd>{ov.scale || '—'}</dd>
                  </div>
                  <div>
                    <dt>基金经理</dt>
                    <dd>{ov.manager || '—'}</dd>
                  </div>
                  <div>
                    <dt>管理人</dt>
                    <dd>{ov.company || '—'}</dd>
                  </div>
                  <div>
                    <dt>托管人</dt>
                    <dd>{ov.custodian || '—'}</dd>
                  </div>
                  <div>
                    <dt>跟踪标的</dt>
                    <dd>{ov.tracking || '—'}</dd>
                  </div>
                  <div className="fund-overview-span">
                    <dt>业绩比较基准</dt>
                    <dd>{ov.benchmark || '—'}</dd>
                  </div>
                  <div className="fund-overview-span">
                    <dt>费率</dt>
                    <dd>
                      管理 {ov.fees.management || '—'} · 托管{' '}
                      {ov.fees.custody || '—'} · 销售{' '}
                      {ov.fees.sales || '—'} · 申购{' '}
                      {ov.fees.subscribe || '—'} · 赎回{' '}
                      {ov.fees.redeem || '—'}
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="table-empty">暂无档案</p>
              )}
            </section>

            <section className="fund-nav" aria-label="单位净值">
              <header className="fund-nav-head">
                <h2>单位净值</h2>
                {latest ? (
                  <p className="fund-nav-latest">
                    <span>{latest.date}</span>
                    <strong className={latestCls}>{formatNav(latest.nav)}</strong>
                    <span className={latestCls}>
                      {formatNavPct(latest.change_pct)}
                    </span>
                  </p>
                ) : null}
              </header>
              {data.nav ? (
                <>
                  <NavChart series={data.nav.series} />
                  <div className="table-wrap fund-nav-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>净值日期</th>
                          <th>单位净值</th>
                          <th>日增长率</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tableRows.map((row) => {
                          const cls = trendClass(row.change_pct)
                          return (
                            <tr key={row.date}>
                              <td>{row.date}</td>
                              <td>{formatNav(row.nav)}</td>
                              <td className={cls}>
                                {formatNavPct(row.change_pct)}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <div className="error-banner">
                  {data.nav_error || '暂无净值数据'}
                </div>
              )}
            </section>
          </>
        ) : null}
      </main>
    </div>
  )
}
