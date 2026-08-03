import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Navigate, useParams, useSearchParams } from 'react-router-dom'
import { KlineChart, type HoverBar } from './components/KlineChart'
import { PageNav } from './components/PageNav'
import { QuotePanel } from './components/QuotePanel'
import { useSources } from './hooks/useSources'
import {
  computeSma,
  fetchKline,
  formatPrice,
  isValidAvgPrice,
  shouldShowAvgPrice,
  type KlineRange,
  type KlineResponse,
} from './klineApi'
import { DEFAULT_SOURCE, hasFeature } from './sources'

const ALL_RANGES: { id: KlineRange; label: string }[] = [
  { id: 'realtime', label: '实时' },
  { id: '5d', label: '5日' },
  { id: 'daily', label: '日K' },
  { id: 'weekly', label: '周K' },
  { id: 'monthly', label: '月K' },
]

const YF_SYMBOL_RE = /^[A-Z0-9.^_=/-]{1,32}$/

/** BaoStock 无真·分时，实时用当日 5 分钟线近似 */
function rangesForSource(source: string) {
  return ALL_RANGES.map((r) =>
    source === 'baostock' && r.id === 'realtime'
      ? { ...r, label: '分时(5分)' }
      : r,
  )
}

function defaultSymbol(source: string): string {
  return source === 'yfinance' ? 'AAPL' : '000001'
}

function normalizeSymbolInput(source: string, raw: string): string {
  if (source === 'yfinance') {
    return raw.trim().toUpperCase().slice(0, 32)
  }
  return raw.replace(/\D/g, '').slice(0, 6)
}

function readInitial(sp: URLSearchParams, source: string) {
  const fallback = defaultSymbol(source)
  const fromUrl = sp.get('symbol')
  const symbol = fromUrl
    ? normalizeSymbolInput(source, fromUrl) || fallback
    : fallback
  const range = (sp.get('range') || 'daily') as KlineRange
  const ok = ALL_RANGES.some((r) => r.id === range)
  return { symbol, range: ok ? range : ('daily' as KlineRange) }
}

export default function KlinePage() {
  const routeParams = useParams()
  const source = (routeParams.source || DEFAULT_SOURCE).toLowerCase()
  const sources = useSources()
  const sourceMeta = sources.find((s) => s.id === source)
  const ranges = useMemo(() => rangesForSource(source), [source])
  const [searchParams, setSearchParams] = useSearchParams()
  const initial = useMemo(
    () => readInitial(searchParams, source),
    [searchParams, source],
  )
  const [symbolInput, setSymbolInput] = useState(initial.symbol)
  const [symbol, setSymbol] = useState(initial.symbol)
  const [range, setRange] = useState<KlineRange>(initial.range)
  const [data, setData] = useState<KlineResponse | null>(null)
  const [hover, setHover] = useState<HoverBar | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Sync from URL (back/forward or external link)
  useEffect(() => {
    const next = readInitial(searchParams, source)
    setSymbolInput(next.symbol)
    setSymbol(next.symbol)
    setRange(next.range)
  }, [searchParams, source])

  const load = useCallback(async (sym: string, r: KlineRange) => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchKline(sym, r, source)
      setHover(null)
      setData(result)
    } catch (err) {
      setData(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [source])

  useEffect(() => {
    void load(symbol, range)
  }, [symbol, range, load])

  // Auto refresh realtime every 30s
  useEffect(() => {
    if (range !== 'realtime') return
    const t = window.setInterval(() => {
      void load(symbol, range)
    }, 30000)
    return () => window.clearInterval(t)
  }, [range, symbol, load])

  const commit = (sym: string, r: KlineRange) => {
    const clean = normalizeSymbolInput(source, sym)
    if (source === 'yfinance') {
      if (!YF_SYMBOL_RE.test(clean)) {
        setError('请输入有效代码，例如 AAPL / MSFT / ^GSPC')
        return
      }
    } else if (clean.length !== 6) {
      setError('请输入 6 位 A 股代码')
      return
    }
    setSymbolInput(clean)
    setSymbol(clean)
    setRange(r)
    setSearchParams({ symbol: clean, range: r }, { replace: true })
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    commit(symbolInput, range)
  }

  const active = useMemo<HoverBar | null>(() => {
    if (hover) return hover
    const base = data?.last
    if (!base) return null
    const withAvg: HoverBar = {
      ...base,
      avgPrice:
        range === 'realtime' && isValidAvgPrice(base.avg_price)
          ? base.avg_price
          : undefined,
    }
    if (range !== 'daily' || !data?.bars?.length) return withAvg
    const i = data.bars.length - 1
    return {
      ...withAvg,
      ma5: computeSma(data.bars, 5)[i],
      ma10: computeSma(data.bars, 10)[i],
      ma20: computeSma(data.bars, 20)[i],
    }
  }, [hover, data, range])
  const showAvgPrice = data
    ? shouldShowAvgPrice(range, data.bars)
    : false
  const change = active && data?.pre_close != null && !hover
    ? active.close - data.pre_close
    : active
      ? active.close - active.open
      : null
  const changePct =
    change != null && active
      ? (change / ((hover ? active.open : (data?.pre_close ?? active.open)) || 1)) * 100
      : null

  if (sourceMeta && !hasFeature(sourceMeta, 'kline')) {
    return <Navigate to={`/${source}`} replace />
  }

  return (
    <div className="app kline-app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1>{sourceMeta?.label || source} K线</h1>
            <p className="brand-sub">
              {source === 'baostock'
                ? '分时(5分) · 5日 · 日K · 周K · 月K · 前复权'
                : source === 'yfinance'
                  ? '实时 · 5日 · 日K · 周K · 月K · Yahoo Finance'
                  : '实时 · 5日 · 日K · 周K · 月K'}
            </p>
          </div>
        </div>
        <PageNav sources={sources} activeFeature="kline" />
      </header>

      <main className="kline-main">
        <form className="kline-toolbar" onSubmit={onSubmit}>
          <label className="kline-symbol">
            <span>代码</span>
            <input
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value)}
              placeholder={source === 'yfinance' ? 'AAPL' : '000001'}
              maxLength={source === 'yfinance' ? 32 : 8}
              inputMode={source === 'yfinance' ? 'text' : 'numeric'}
              autoCapitalize="characters"
              spellCheck={false}
            />
          </label>
          <div className="range-tabs" role="tablist" aria-label="周期">
            {ranges.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={range === item.id}
                className={range === item.id ? 'active' : ''}
                onClick={() => commit(symbolInput || symbol, item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? '加载中…' : '查询'}
          </button>
        </form>

        <section className="kline-panel">
          <div className="kline-head">
            <div>
              <h2>
                <span className="kline-name">{data?.name || '—'}</span>
                <code>{symbol}</code>
              </h2>
              {active ? (
                <>
                  <p className="kline-quote">
                    <strong
                      className={
                        change == null ? '' : change >= 0 ? 'up' : 'down'
                      }
                    >
                      {formatPrice(active.close, symbol)}
                    </strong>
                    {change != null && changePct != null ? (
                      <span className={change >= 0 ? 'up' : 'down'}>
                        {change >= 0 ? '+' : ''}
                        {formatPrice(change, symbol)} ({changePct >= 0 ? '+' : ''}
                        {changePct.toFixed(2)}%)
                      </span>
                    ) : null}
                    <span className="meta-line">
                      {active.time}
                      {' · '}
                      {ranges.find((r) => r.id === range)?.label}
                      {data?.source ? ` · ${data.source}` : ''}
                      {data ? ` · ${data.count} 根` : ''}
                    </span>
                  </p>
                  <p className="kline-ohlc">
                    <span>开 {formatPrice(active.open, symbol)}</span>
                    <span>高 {formatPrice(active.high, symbol)}</span>
                    <span>低 {formatPrice(active.low, symbol)}</span>
                    <span>收 {formatPrice(active.close, symbol)}</span>
                    {active.volume != null ? (
                      <span>量 {Math.round(active.volume).toLocaleString()}</span>
                    ) : null}
                    {range === 'realtime' && active.avgPrice != null ? (
                      <span className="kline-ma kline-avg">
                        均价 {formatPrice(active.avgPrice, symbol)}
                      </span>
                    ) : null}
                    {range === 'daily' && active.ma5 != null ? (
                      <span className="kline-ma kline-ma5">
                        MA5 {formatPrice(active.ma5, symbol)}
                      </span>
                    ) : null}
                    {range === 'daily' && active.ma10 != null ? (
                      <span className="kline-ma kline-ma10">
                        MA10 {formatPrice(active.ma10, symbol)}
                      </span>
                    ) : null}
                    {range === 'daily' && active.ma20 != null ? (
                      <span className="kline-ma kline-ma20">
                        MA20 {formatPrice(active.ma20, symbol)}
                      </span>
                    ) : null}
                  </p>
                  {range === 'daily' ? (
                    <p className="kline-ma-legend" aria-hidden>
                      <span className="kline-ma kline-ma5">MA5</span>
                      <span className="kline-ma kline-ma10">MA10</span>
                      <span className="kline-ma kline-ma20">MA20</span>
                    </p>
                  ) : null}
                  {showAvgPrice ? (
                    <p className="kline-ma-legend" aria-hidden>
                      <span className="kline-ma kline-avg">均价</span>
                    </p>
                  ) : null}
                </>
              ) : (
                <p className="kline-quote muted">选择代码后查看行情</p>
              )}
            </div>
          </div>

          {error ? <div className="error-banner" style={{ margin: '0 1rem' }}>{error}</div> : null}

          <div
            className={
              source === 'akshare' ? 'kline-body with-quote' : 'kline-body'
            }
          >
            <div className="kline-chart-shell">
              {loading && !data ? (
                <div className="table-empty">正在拉取 K 线…</div>
              ) : (
                <KlineChart
                  data={data}
                  onHover={(bar) => {
                    setHover((prev) => {
                      if (prev?.time === bar?.time) return prev
                      return bar
                    })
                  }}
                />
              )}
            </div>
            {source === 'akshare' ? (
              <QuotePanel symbol={symbol} source="akshare" />
            ) : null}
          </div>
        </section>
      </main>
    </div>
  )
}
