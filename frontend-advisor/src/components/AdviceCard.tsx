import type { AdviceItem, FactorContribution, GraphSignal } from '../api'
import { formatPct, formatScore } from '../api'
import { explorerKlineUrl } from '../explorerLinks'
import { StarToggle } from './StarToggle'

const ACTION_CLASS: Record<string, string> = {
  buy: 'action-buy',
  add: 'action-buy',
  sell: 'action-sell',
  hold: 'action-hold',
  watch: 'action-watch',
}

const GRAPH_LABEL: Record<string, string> = {
  BUY: '图买入',
  HOLD: '图观望',
  SELL: '图卖出',
}

const GRAPH_CLASS: Record<string, string> = {
  BUY: 'action-buy',
  HOLD: 'action-hold',
  SELL: 'action-sell',
}

export function ActionBadge({ action, label }: { action: string; label: string }) {
  return <span className={`action-badge ${ACTION_CLASS[action] || ''}`}>{label}</span>
}

export function GraphSignalBadge({ signal }: { signal?: GraphSignal | null }) {
  if (!signal) return null
  if (signal.error) {
    return (
      <span className="action-badge action-watch graph-badge" title={signal.error}>
        图暂缺
      </span>
    )
  }
  const action = String(signal.action || '').toUpperCase()
  if (!action) return null
  const scores = signal.scores
  const title = scores
    ? `B ${Number(scores.BUY || 0).toFixed(2)} / H ${Number(scores.HOLD || 0).toFixed(2)} / S ${Number(
        scores.SELL || 0,
      ).toFixed(2)}`
    : undefined
  return (
    <span className={`action-badge graph-badge ${GRAPH_CLASS[action] || ''}`} title={title}>
      {GRAPH_LABEL[action] || `图${action}`}
    </span>
  )
}

function GraphSignalNote({ signal }: { signal?: GraphSignal | null }) {
  if (!signal || signal.error) return null
  const scores = signal.scores
  const scoreText = scores
    ? `B ${Number(scores.BUY || 0).toFixed(2)} / H ${Number(scores.HOLD || 0).toFixed(2)} / S ${Number(
        scores.SELL || 0,
      ).toFixed(2)}`
    : null
  const patterns = (signal.patterns || []).slice(0, 4).join('、')
  return (
    <p className="graph-signal-note muted">
      图学习
      {signal.horizon_days ? `（${signal.horizon_days}日）` : ''}
      {scoreText ? ` · ${scoreText}` : ''}
      {signal.market_regime ? ` · ${signal.market_regime}` : ''}
      {patterns ? ` · ${patterns}` : ''}
      {signal.blocked_reason ? ` · 受限：${signal.blocked_reason}` : ''}
    </p>
  )
}

export function FactorBars({ factors }: { factors: FactorContribution[] }) {
  return (
    <ul className="factor-bars">
      {factors.map((f) => (
        <li key={f.name}>
          <span className="factor-name">{f.name}</span>
          <span className="factor-track">
            <span
              className="factor-fill"
              style={{ width: `${Math.round(f.normalized * 100)}%` }}
            />
          </span>
          <span className="factor-val">{f.normalized.toFixed(2)}</span>
        </li>
      ))}
    </ul>
  )
}

export function AdviceCard({
  item,
  starred,
  starBusy,
  onToggleStar,
}: {
  item: AdviceItem
  starred?: boolean
  starBusy?: boolean
  onToggleStar?: (next: boolean) => void | Promise<void>
}) {
  return (
    <article className="advice-card">
      <header className="advice-card-head">
        <div>
          <h2>
            <span className="mono">{item.symbol}</span> {item.name}
          </h2>
          <p className="muted">
            {item.as_of ? `截至 ${item.as_of}` : ''}
            {item.close != null ? ` · 收盘 ${item.close}` : ''}
          </p>
        </div>
        <div className="advice-card-badges">
          <ActionBadge action={item.action} label={item.action_label} />
          <GraphSignalBadge signal={item.graph_signal} />
        </div>
      </header>
      <div className="advice-metrics">
        <div>
          <span className="metric-label">评分</span>
          <strong className="metric-value">{formatScore(item.score)}</strong>
        </div>
        <div>
          <span className="metric-label">历史命中率</span>
          <strong className="metric-value">{formatPct(item.hit_rate)}</strong>
        </div>
        <div>
          <span className="metric-label">持仓</span>
          <strong className="metric-value">{item.has_position ? '有' : '无'}</strong>
        </div>
      </div>
      {item.rationale ? <p className="rationale">{item.rationale}</p> : null}
      <GraphSignalNote signal={item.graph_signal} />
      {item.factors?.length ? <FactorBars factors={item.factors} /> : null}
      <footer className="advice-card-actions">
        {onToggleStar ? (
          <StarToggle
            symbol={item.symbol}
            starred={Boolean(starred)}
            busy={starBusy}
            onToggle={onToggleStar}
          />
        ) : null}
        <a
          className="text-link"
          href={explorerKlineUrl(item.symbol)}
          target="_blank"
          rel="noreferrer"
        >
          查看K线
        </a>
      </footer>
    </article>
  )
}
