import { formatPct, type PaperAccount } from '../api'

function display(value: unknown) {
  if (value == null || value === '') return '—'
  return String(value)
}

function displayMoney(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : display(value)
}

function displayTradeSide(value: unknown) {
  if (String(value) === 'buy') return '买入'
  if (String(value) === 'sell') return '卖出'
  return display(value)
}

function CardMetrics(props: { children: React.ReactNode }) {
  return <dl className="paper-card-metrics">{props.children}</dl>
}

function Metric(props: { label: string; value: React.ReactNode; className?: string }) {
  return (
    <div>
      <dt>{props.label}</dt>
      <dd className={props.className}>{props.value}</dd>
    </div>
  )
}

export function PaperPositionCard(props: {
  position: PaperAccount['positions'][number]
  onSell: (position: PaperAccount['positions'][number]) => void
  onDelete: (position: PaperAccount['positions'][number]) => void
}) {
  const { position } = props
  const title = position.name || position.symbol
  return (
    <article className="paper-card paper-card--position" aria-label={`${title} ${position.symbol}`}>
      <div className="paper-card-head">
        <div>
          <h3>{title}</h3>
          <p className="mono">{position.symbol}</p>
        </div>
      </div>
      <CardMetrics>
        <Metric label="数量" value={display(position.qty)} />
        <Metric label="成本" value={display(position.cost)} />
        <Metric label="现价" value={display(position.last)} />
        <Metric label="市值" value={displayMoney(position.market_value)} />
        <Metric label="浮盈亏" value={displayMoney(position.pnl)} />
        <Metric
          label="浮盈亏率"
          value={formatPct(position.pnl_pct, 2)}
          className={(position.pnl_pct || 0) >= 0 ? 'up' : 'down'}
        />
      </CardMetrics>
      <div className="paper-card-actions">
        <button type="button" className="text-link" onClick={() => props.onSell(position)}>
          卖出
        </button>
        <button
          type="button"
          className="text-link danger"
          onClick={() => props.onDelete(position)}
        >
          删除
        </button>
      </div>
    </article>
  )
}

export function PaperPerformanceCard(props: { row: Record<string, unknown> }) {
  const { row } = props
  const title = display(row.name)
  const symbol = display(row.symbol)
  return (
    <article className="paper-card paper-card--performance" aria-label={`${title} ${symbol}`}>
      <div className="paper-card-head">
        <div>
          <h3>{title}</h3>
          <p className="mono">{symbol}</p>
        </div>
      </div>
      <CardMetrics>
        <Metric label="推荐日" value={display(row.rec_date)} />
        <Metric label="买价" value={display(row.buy_price)} />
        <Metric label="现价" value={display(row.last)} />
        <Metric label="浮盈亏" value={formatPct(row.unrealized_pnl_pct as number | null, 2)} />
      </CardMetrics>
    </article>
  )
}

export function PaperTradeCard(props: { trade: Record<string, unknown> }) {
  const { trade } = props
  const title = display(trade.name)
  const symbol = display(trade.symbol)
  const createdAt = display(trade.created_at).slice(0, 19).replace('T', ' ')
  return (
    <article className="paper-card paper-card--trade" aria-label={`${title} ${symbol}`}>
      <div className="paper-card-head">
        <div>
          <h3>{title}</h3>
          <p className="mono">{symbol}</p>
        </div>
      </div>
      <CardMetrics>
        <Metric label="时间" value={createdAt || '—'} />
        <Metric label="方向" value={displayTradeSide(trade.side)} />
        <Metric label="数量" value={display(trade.qty)} />
        <Metric label="价格" value={display(trade.price)} />
        <Metric label="来源" value={display(trade.source)} />
      </CardMetrics>
    </article>
  )
}
