import { useCallback, useEffect, useState } from 'react'
import { formatPrice } from '../klineApi'
import { fetchQuote, type QuoteResponse } from '../quoteApi'

type Props = {
  symbol: string
  source?: string
}

function fmtVol(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`
  return String(Math.round(v))
}

function LevelRow({
  label,
  price,
  volume,
  kind,
  symbol,
  maxVol,
}: {
  label: string
  price: number | null
  volume: number | null
  kind: 'ask' | 'bid'
  symbol: string
  maxVol: number
}) {
  const width =
    volume != null && maxVol > 0 ? Math.min(100, (volume / maxVol) * 100) : 0
  return (
    <div className={`quote-level quote-level-${kind}`}>
      <span className="quote-level-label">{label}</span>
      <span className={`quote-level-price ${kind}`}>
        {price == null ? '—' : formatPrice(price, symbol)}
      </span>
      <span className="quote-level-vol-wrap">
        <span className="quote-level-bar" style={{ width: `${width}%` }} />
        <span className="quote-level-vol">{fmtVol(volume)}</span>
      </span>
    </div>
  )
}

export function QuotePanel({ symbol, source = 'akshare' }: Props) {
  const [data, setData] = useState<QuoteResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    if (!/^\d{6}$/.test(symbol)) return
    setLoading(true)
    try {
      const q = await fetchQuote(symbol, source)
      setData(q)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [symbol, source])

  useEffect(() => {
    void load()
  }, [load])

  // 仅交易时段自动刷新；收盘后只拉取一次（随 symbol 变化）
  useEffect(() => {
    if (!data?.session?.refresh_recommended) return
    const t = window.setInterval(() => {
      void load()
    }, 8000)
    return () => window.clearInterval(t)
  }, [load, data?.session?.refresh_recommended])

  const snap = data?.snapshot
  const asks = snap?.asks ?? [] // 卖五→卖一（上→下）
  const bids = snap?.bids ?? [] // 买一→买五
  const maxVol = Math.max(
    1,
    ...[...asks, ...bids].map((l) => l.volume ?? 0).filter((v) => v > 0),
  )
  const chg = snap?.change_pct
  const chgCls = chg == null || chg === 0 ? '' : chg > 0 ? 'up' : 'down'
  const bookAsOf = snap?.book_as_of
  const sessionLabel = data?.session?.is_trading
    ? '交易中 · 自动刷新'
    : '已收盘 · 手动刷新'

  return (
    <aside className="quote-panel">
      <header className="quote-panel-head">
        <h3>盘口</h3>
        <div className="quote-panel-meta">
          <span>{sessionLabel}</span>
          <button type="button" className="quote-refresh" onClick={() => void load()} disabled={loading}>
            {loading ? '…' : '刷新'}
          </button>
        </div>
      </header>

      {error ? <p className="quote-error">{error}</p> : null}

      {snap ? (
        <div className={`quote-last ${chgCls}`}>
          <strong>{snap.price == null ? '—' : formatPrice(snap.price, symbol)}</strong>
          {chg != null ? (
            <span>
              {chg > 0 ? '+' : ''}
              {chg.toFixed(2)}%
            </span>
          ) : null}
        </div>
      ) : null}

      {data?.book_note ? <p className="quote-hint">{data.book_note}</p> : null}
      {bookAsOf ? (
        <p className="quote-hint">五档时间 {bookAsOf}</p>
      ) : null}
      {!data?.book_available ? (
        <p className="quote-hint">暂无五档数据。</p>
      ) : null}

      <div className="quote-book">
        {asks.map((lv, i) => (
          <LevelRow
            key={`ask-${i}`}
            label={`卖${5 - i}`}
            price={lv.price}
            volume={lv.volume}
            kind="ask"
            symbol={symbol}
            maxVol={maxVol}
          />
        ))}
        <div className="quote-book-split" />
        {bids.map((lv, i) => (
          <LevelRow
            key={`bid-${i}`}
            label={`买${i + 1}`}
            price={lv.price}
            volume={lv.volume}
            kind="bid"
            symbol={symbol}
            maxVol={maxVol}
          />
        ))}
      </div>

      <dl className="quote-stats">
        <div>
          <dt>外盘</dt>
          <dd>{fmtVol(snap?.outer_vol)}</dd>
        </div>
        <div>
          <dt>内盘</dt>
          <dd>{fmtVol(snap?.inner_vol)}</dd>
        </div>
        <div>
          <dt>量比</dt>
          <dd>{snap?.volume_ratio == null ? '—' : snap.volume_ratio.toFixed(2)}</dd>
        </div>
        <div>
          <dt>换手</dt>
          <dd>{snap?.turnover == null ? '—' : `${snap.turnover.toFixed(2)}%`}</dd>
        </div>
      </dl>

      <h4 className="quote-ticks-title">分时成交</h4>
      <div className="quote-ticks">
        {(data?.ticks ?? []).length === 0 ? (
          <p className="quote-hint">暂无成交明细</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>价格</th>
                <th>手数</th>
                <th>性质</th>
              </tr>
            </thead>
            <tbody>
              {(data?.ticks ?? []).map((t, idx) => {
                const sideCls =
                  t.side === '买盘' ? 'up' : t.side === '卖盘' ? 'down' : ''
                return (
                  <tr key={`${t.time}-${idx}`}>
                    <td>{t.time}</td>
                    <td className={sideCls}>
                      {t.price == null ? '—' : formatPrice(t.price, symbol)}
                    </td>
                    <td>{fmtVol(t.volume)}</td>
                    <td className={sideCls}>{t.side}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </aside>
  )
}
