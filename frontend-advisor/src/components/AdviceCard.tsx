import type { AdviceItem, FactorContribution } from '../api'
import { formatPct, formatScore } from '../api'

const ACTION_CLASS: Record<string, string> = {
  buy: 'action-buy',
  add: 'action-buy',
  sell: 'action-sell',
  hold: 'action-hold',
  watch: 'action-watch',
}

export function ActionBadge({ action, label }: { action: string; label: string }) {
  return <span className={`action-badge ${ACTION_CLASS[action] || ''}`}>{label}</span>
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

export function AdviceCard({ item }: { item: AdviceItem }) {
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
        <ActionBadge action={item.action} label={item.action_label} />
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
      {item.factors?.length ? <FactorBars factors={item.factors} /> : null}
    </article>
  )
}
